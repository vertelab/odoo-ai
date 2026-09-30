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
        help='Periodens första dag (dag eller månad beroende på granularity).')
    granularity = fields.Selection([
        ('day', 'Dag'),
        ('month', 'Månad'),
    ], string='Granularitet', default='month', required=True, index=True)

    company_id = fields.Many2one(
        'res.company', default=lambda self: self.env.company, index=True)
    currency_id = fields.Many2one(
        'res.currency', string='Valuta',
        default=lambda self: self.env.company.currency_id)

    # ── De två talen — aldrig hopslagna ──────────────────────────────
    cost_usd = fields.Float(
        'Faktisk kostnad (USD)', digits=(16, 8),
        help='Σ cost_usd för periodens rader. Faktisk provider-kostnad.')
    cost_company = fields.Float(
        'Faktisk kostnad (bolagsvaluta)', digits=(16, 4),
        help='cost_usd omräknad till bolagsvalutan per RADENS datum — '
             'en senare kursändring ändrar inte historiska perioder.')
    billing_base = fields.Float(
        'Debiteringsgrund', digits=(16, 4),
        help='Σ token_sys × sys_multiplier — den marginal-påslagna grunden. '
             'INTE samma tal som cost_usd.')

    # ── Ärlighetsflagga ──────────────────────────────────────────────
    line_count = fields.Integer('Rader')
    unreported_line_count = fields.Integer(
        'Omätta rader',
        help='Rader med usage_reported=False. Beloppet är ett minimum.')
    has_unreported = fields.Boolean(
        'Har omätta rader', compute='_compute_has_unreported', store=True)

    # ── Attribution (projekt/kund) ───────────────────────────────────
    partner_id = fields.Many2one('res.partner', string='Kund', index=True)
    project_id = fields.Many2one('project.project', string='Projekt', index=True)
    task_id = fields.Many2one('project.task', string='Uppgift', index=True)
    context_confirmed = fields.Boolean(
        'Kostnadskontext bekräftad',
        help='False = raderna saknade bekräftad kostnadskontext. Den '
             'okontextade andelen döljs inte — den är en egen post.')

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
        """
        group_by = group_by or []
        domain = [('usage_reported', '!=', None)]  # alla rader
        if date_from:
            domain.append(('create_date', '>=', date_from))
        if date_to:
            domain.append(('create_date', '<=', date_to))
        lines = self.env['ai.coworker.session.line'].search(domain)

        company_currency = self.env.company.currency_id
        buckets = {}
        for ln in lines:
            sess = ln.session_id
            start = self._period_start(ln.create_date, granularity)
            key = (start,)
            vals = {}
            if 'partner' in group_by:
                vals['partner_id'] = sess.partner_id.id or False
                key += (vals['partner_id'],)
            if 'project' in group_by:
                # Domänfält — läggs av bryggor (project_ai) via arv. Saknas
                # de blir posten okontextad, inte felaktig.
                proj = sess.project_id if 'project_id' in sess._fields else False
                vals['project_id'] = proj.id if proj else False
                key += (vals['project_id'],)
            if 'task' in group_by:
                task = sess.task_id if 'task_id' in sess._fields else False
                vals['task_id'] = task.id if task else False
                key += (vals['task_id'],)
            b = buckets.setdefault(key, {
                'period_start': start, 'granularity': granularity,
                'cost_usd': 0.0, 'cost_company': 0.0, 'billing_base': 0.0,
                'line_count': 0, 'unreported_line_count': 0,
                'context_confirmed': bool(sess.cost_context_confirmed),
                **vals,
            })
            b['line_count'] += 1
            if not ln.usage_reported:
                b['unreported_line_count'] += 1
                continue
            b['cost_usd'] += ln.cost_usd or 0.0
            b['billing_base'] += ln.token_sys or 0
            # Omräkning per RADENS datum (inte dagens kurs).
            try:
                b['cost_company'] += company_currency._convert(
                    ln.cost_usd or 0.0,
                    self.env.company.currency_id,
                    self.env.company,
                    ln.create_date.date() if ln.create_date else fields.Date.today(),
                )
            except Exception:
                # Ingen kurs tillgänglig → lämna 0 men behåll cost_usd.
                pass
        return list(buckets.values())

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
