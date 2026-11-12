# -*- coding: utf-8 -*-
"""ai.cost.period.line — AI-kostnad aggregerad per period och valuta.

Bryggan mellan `ai.coworker.session.line` och företagets likviditetsprognos
(ai-kostnad-till-likviditet). Två tal hålls ALLTID åtskilda:

- **Faktisk kostnad** (`cost_usd` → SEK per radens datum) — vad AI:n kostade.
- **Debiteringsgrund** (`token_sys` × `sys_multiplier`) — vad kunden debiteras,
  inkl. Vertel-marginal. Inte samma tal.

Att slå ihop dem vore att felaktigt fakturera eller felaktigt prognostisera.
Omätta rader (`usage_reported=False`) bärs som `unreported_line_count` +
`has_unreported`, så en konsument kan visa "minst X kr" i stället för ett
falskt exakt tal.

Kärnan förblir domän-ren: ingen `liquidity_*`-modell refereras här. Aggregatet
är den Odoo-ai-sida som kan fylla prognosens kontrakt.
"""

import logging

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class AiCostPeriodLine(models.Model):
    _name = 'ai.cost.period.line'
    _description = 'AI-kostnad per period och valuta'
    _order = 'period_start desc, id desc'

    period_start = fields.Date(
        'Period start', required=True, index=True,
        help='The first day of the period (day or month depending on granularity).')
    granularity = fields.Selection([
        ('day', 'Dag'),
        ('month', 'Month'),
    ], string='Granularitet', default='month', required=True, index=True)

    company_id = fields.Many2one(
        'res.company', default=lambda self: self.env.company, index=True)
    currency_id = fields.Many2one(
        'res.currency', string='Valuta',
        default=lambda self: self.env.company.currency_id)

    # ── De två talen — aldrig hopslagna ──────────────────────────────
    cost_usd = fields.Float(
        'Faktisk kostnad (USD)', digits=(16, 8),
        help='Sum of cost_usd for the period rows. Actual provider cost.')
    cost_company = fields.Float(
        'Faktisk kostnad (bolagsvaluta)', digits=(16, 4),
        help='cost_usd converted to the company currency at the ROW date — a later rate change does not alter historical periods.')
    billing_base = fields.Float(
        'Debiteringsgrund', digits=(16, 4),
        help='Sum of token_sys x sys_multiplier — the margin-loaded basis. NOT the same number as cost_usd.')

    # ── Ärlighetsflagga ──────────────────────────────────────────────
    line_count = fields.Integer('Rader')
    unreported_line_count = fields.Integer(
        'Unmeasured Rows',
        help='Rows with usage_reported=False. The amount is a minimum.')
    has_unreported = fields.Boolean(
        'Has Unmeasured Rows', compute='_compute_has_unreported', store=True)

    # ── Attribution (projekt/kund) ───────────────────────────────────
    partner_id = fields.Many2one('res.partner', string='Kund', index=True)
    project_id = fields.Many2one('project.project', string='Projekt', index=True)
    task_id = fields.Many2one('project.task', string='Uppgift', index=True)
    context_confirmed = fields.Boolean(
        'Cost Context Confirmed',
        help='False = the rows had no confirmed cost context. The uncontexted share is not hidden — it is its own entry.')

    @api.depends('unreported_line_count')
    def _compute_has_unreported(self):
        for r in self:
            r.has_unreported = bool(r.unreported_line_count)

    # ── Aggregering ──────────────────────────────────────────────────

    @api.model
    def _period_start(self, dt, granularity):
        """Första dagen i perioden för ett datum."""
        if granularity == 'month':
            return dt.date().replace(day=1)
        return dt.date()

    @api.model
    def _aggregate(self, date_from=None, date_to=None, granularity='month',
                   group_by=None):
        """Aggregera session lines per period (och valfritt projekt/kund).

        Returnerar en lista dictar — skriver INTE automatiskt. Anroparen
        (cron eller likviditetsbryggan) bestämmer när raderna sparas.

        `group_by`: lista ur ('partner', 'project', 'task'). Utan den blir
        det en post per period.

        En period utan rader ger INGEN post (inte en nollkostnad).

        AGGREGERAR I SQL, INTE I PYTHON (2026-11-12). Den tidigare versionen
        gjorde `search(domain)` utan limit och läste `ln.session_id` per rad.
        Det drog in hela perioden i minnet: 163 994 rader för innevarande
        månad, ur en tabell på 2,3 GB och 619 618 rader totalt. Processen
        dog på `psycopg2.DatabaseError: out of memory for query result`, och
        eftersom det var en DATABASfel och inte ett vanligt undantag stängdes
        anslutningen — vilket i sin tur fick `_record_failure` att kasta
        `InterfaceError: connection already closed` och stoppade HELA
        cron-ticken. En kostnadsrapport tog ner schemaläggaren.

        Nu görs grupperingen i databasen: en rad per (period, valfritt
        partner/projekt/task) i stället för en Python-rad per session line.
        Mängden som lämnar PostgreSQL är antalet BUCKETS — normalt ett
        fåtal — inte antalet rader.

        `cost_company` räknas fortfarande i Python, eftersom växelkursen ska
        vara radens datum och kursen hämtas via ORM:en. Det sker nu på
        bucket-nivå (en konvertering per period och valuta), inte per rad.
        """
        group_by = group_by or []

        # ── Grupperingskolumner ─────────────────────────────────────────
        #
        # Fälten project_id/task_id läggs på `ai.coworker.session` av
        # bryggor (project_ai) via arv. Saknas de blir posten okontextad,
        # inte felaktig — samma kontrakt som tidigare.
        Session = self.env['ai.coworker.session']
        select_parts = []
        group_parts = []
        if 'partner' in group_by:
            select_parts.append('s.partner_id AS partner_id')
            group_parts.append('s.partner_id')
        if 'project' in group_by and 'project_id' in Session._fields:
            select_parts.append('s.project_id AS project_id')
            group_parts.append('s.project_id')
        if 'task' in group_by and 'task_id' in Session._fields:
            select_parts.append('s.task_id AS task_id')
            group_parts.append('s.task_id')

        # Periodens start beräknas i SQL så att grupperingen sker på samma
        # nyckel som `_period_start()` ger i Python.
        if granularity == 'month':
            period_expr = "date_trunc('month', l.create_date)::date"
        else:
            period_expr = 'l.create_date::date'
        select_parts.insert(0, '%s AS period_start' % period_expr)
        group_parts.insert(0, period_expr)

        where = ['l.usage_reported IS NOT NULL']
        params = []
        if date_from:
            where.append('l.create_date >= %s')
            params.append(date_from)
        if date_to:
            where.append('l.create_date <= %s')
            params.append(date_to)

        # `usage_reported` är ett booleskt fält: `IS NOT NULL` motsvarar det
        # gamla `('usage_reported', '!=', None)` — alla rader som har ett
        # värde, oavsett om det är sant eller falskt.
        sql = """
            SELECT %(select)s,
                   count(*) AS line_count,
                   count(*) FILTER (WHERE l.usage_reported = false)
                       AS unreported_line_count,
                   coalesce(sum(l.cost_usd) FILTER
                       (WHERE l.usage_reported = true), 0) AS cost_usd,
                   coalesce(sum(l.token_sys) FILTER
                       (WHERE l.usage_reported = true), 0) AS billing_base,
                   bool_or(s.cost_context_confirmed) AS context_confirmed
              FROM ai_coworker_session_line l
              JOIN ai_coworker_session s ON s.id = l.session_id
             WHERE %(where)s
          GROUP BY %(group)s
        """ % {
            'select': ', '.join(select_parts),
            'where': ' AND '.join(where),
            'group': ', '.join(group_parts),
        }

        self.env.cr.execute(sql, params)
        rows = self.env.cr.dictfetchall()

        company_currency = self.env.company.currency_id
        company = self.env.company
        today = fields.Date.today()
        result = []
        for row in rows:
            cost_usd = float(row['cost_usd'] or 0.0)
            # Omräkning per PERIOD (inte dagens kurs). Kursen slås upp en
            # gång per bucket; `_convert` är cache-ad i Odoo, så en period
            # med samma datum kostar ett uppslag, inte ett per rad.
            try:
                cost_company = company_currency._convert(
                    cost_usd, company_currency, company,
                    row['period_start'] or today)
            except Exception:
                # Ingen kurs tillgänglig → lämna 0 men behåll cost_usd.
                cost_company = 0.0
            vals = {
                'period_start': row['period_start'],
                'granularity': granularity,
                'cost_usd': cost_usd,
                'cost_company': cost_company,
                'billing_base': float(row['billing_base'] or 0.0),
                'line_count': row['line_count'],
                'unreported_line_count': row['unreported_line_count'],
                'context_confirmed': bool(row['context_confirmed']),
            }
            for key in ('partner_id', 'project_id', 'task_id'):
                if key in row:
                    vals[key] = row[key] or False
            result.append(vals)
        return result

    @api.model
    def cron_aggregate(self, granularity='month'):
        """Cron: bygg om aggregatet för innevarande och förra perioden.

        Idempotent: rader för perioden raderas och skrivs på nytt, så en
        senare körning ger samma resultat (ingen dubbelräkning).
        """
        from datetime import date, timedelta
        today = date.today()
        if granularity == 'month':
            starts = [today.replace(day=1),
                      (today.replace(day=1) - timedelta(days=1)).replace(day=1)]
        else:
            starts = [today, today - timedelta(days=1)]

        total = 0
        for start in starts:
            rows = self._aggregate(
                date_from=start,
                date_to=start + timedelta(days=31 if granularity == 'month' else 1),
                granularity=granularity)
            self.search([
                ('period_start', '=', start),
                ('granularity', '=', granularity),
            ]).unlink()
            for r in rows:
                if r['period_start'] == start:
                    self.create(r)
                    total += 1
        _logger.info('AI-kostnadsaggregat: %d poster (%s)', total, granularity)
        return total
