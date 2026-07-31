# -*- coding: utf-8 -*-
"""Odoo Mind Dashboard — admin-övervakning + manuella körningar (task 5.3).

Övervakning: graph-status, bridges, minne, cron-jobb.
Manuella körningar: indexera allt / ändrade denna månad / ändrade idag /
per kategori (artifact type) — alla via samma _okf_upsert()-path.
"""

import logging
from datetime import datetime, timedelta

from odoo import models, fields, api, _

_logger = logging.getLogger(__name__)


# ── Cron → artefakttyp (company memory-indexerare) ──
# Statisk karta: cron_namn innehåller nyckelordet → typ.
_TYPE_CRON_KEYWORDS = {
    'partner': ['partner customer'],
    'supplier': ['partner supplier'],
    'knowledge': ['knowledge article'],
    'document': ['dms document'],
    'website': ['website rag'],
    'strategy': ['strategy'],
    'mgmt_summary': ['management summary'],
    # learning (memory) hanteras av OKF dirty-cron — inga egna crons
}


class AIOkfDashboardLine(models.TransientModel):
    """Dashboard-rad per kategori (ai.artifact.type).

    Värdena sätts i batchen i ai.okf.dashboard._compute_line_ids så att
    dashboarden öppnar snabbt (ett fåtal SQL/group-by, inte per-rad-sök).
    """
    _name = 'ai.okf.dashboard.line'
    _description = 'Odoo Mind Dashboard rad (per kategori)'
    _order = 'kind, name'

    dashboard_id = fields.Many2one(
        'ai.okf.dashboard', string='Dashboard', ondelete='cascade')
    artifact_type_id = fields.Many2one(
        'ai.artifact.type', string='Artefakttyp')
    name = fields.Char(string='Kategori')
    kind = fields.Selection([
        ('memory', 'Memory'),
        ('knowledge', 'Knowledge'),
    ], string='Typ')
    bridge_module = fields.Char(string='Bridge-modul')

    health = fields.Selection([
        ('green', '🟢 Grön'),
        ('yellow', '🟡 Gul'),
        ('red', '🔴 Röd'),
    ], string='Hälsa')
    health_reason = fields.Char(string='Orsak')

    concept_count = fields.Integer(string='Koncept')
    dirty_count = fields.Integer(string='Dirty')
    superseded_count = fields.Integer(string='Superseded')
    stale_count = fields.Integer(string='Stale')
    node_count = fields.Integer(string='AGE-noder')
    size_estimate = fields.Char(string='Storlek')

    indexer_tasks = fields.Text(string='Indexer-uppgifter')
    last_run = fields.Datetime(string='Senaste körning')
    cron_failures = fields.Integer(string='Cron-fel')

    def _open_dashboard(self):
        self.ensure_one()
        return self.dashboard_id._reopen()

    def action_index_dirty(self):
        """Indexera dirty-artefakter för denna kategori."""
        self.ensure_one()
        self.dashboard_id._run_okf_index(
            artifact_types=self.artifact_type_id)
        return self._open_dashboard()

    def action_index_all(self):
        """Indexera alla dirty-artefakter för denna kategori (alla tider)."""
        self.ensure_one()
        self.dashboard_id._run_okf_index(
            artifact_types=self.artifact_type_id)
        return self._open_dashboard()

    def action_index_month(self):
        self.ensure_one()
        self.dashboard_id._run_okf_index(
            artifact_types=self.artifact_type_id, dirty_scope='month')
        return self._open_dashboard()

    def action_index_today(self):
        self.ensure_one()
        self.dashboard_id._run_okf_index(
            artifact_types=self.artifact_type_id, dirty_scope='today')
        return self._open_dashboard()


class AIOkfDashboard(models.TransientModel):
    _name = 'ai.okf.dashboard'
    _description = 'Odoo Mind Dashboard'

    # ── Status ──
    graph_status = fields.Char(compute='_compute_stats', string='Graph Status')
    graph_version = fields.Char(compute='_compute_stats', string='AGE Version')
    concept_count = fields.Integer(compute='_compute_stats', string='Koncept')
    memory_count = fields.Integer(compute='_compute_stats', string='AI Memories')
    company_concept_count = fields.Integer(compute='_compute_stats')
    personal_concept_count = fields.Integer(compute='_compute_stats')
    coworker_concept_count = fields.Integer(compute='_compute_stats')
    dirty_count = fields.Integer(compute='_compute_stats', string='Dirty')
    superseded_count = fields.Integer(compute='_compute_stats')
    cron_status = fields.Text(compute='_compute_stats', string='Cron Status')

    # ── Manuella körningar ──
    artifact_type_ids = fields.Many2many(
        'ai.artifact.type', string='Kategori (artefakttyp)')
    run_result = fields.Text(string='Resultat')

    # ── Per-kategori-rader (change ai-orchestration-tidy-up, g) ──
    line_ids = fields.One2many(
        'ai.okf.dashboard.line', 'dashboard_id',
        compute='_compute_line_ids', string='Kategorier')

    @api.depends()
    def _compute_line_ids(self):
        """Bygg en rad per aktiv artefakttyp med hälsa + räkningar i batch.

        Alla räkningar görs med några få SQL/group-by-frågor istället för
        per-rad-sökningar → dashboarden öppnar snabbt.
        """
        types = self.env['ai.artifact.type'].search([('active', '=', True)])
        if not types:
            for rec in self:
                rec.line_ids = [(5, 0, 0)]
            return

        type_ids = tuple(types.ids)

        # 1. Koncept-räkningar (total/dirty/superseded) i EN fråga
        self.env.cr.execute("""
            SELECT artifact_type_id,
                   count(*) AS total,
                   count(*) FILTER (WHERE dirty) AS dirty,
                   count(*) FILTER (WHERE status = 'superseded') AS superseded
            FROM ai_okf_concept
            WHERE artifact_type_id IN %s
            GROUP BY artifact_type_id
        """, (type_ids,))
        stats = {row[0]: {'total': row[1], 'dirty': row[2],
                          'superseded': row[3]}
                 for row in self.env.cr.fetchall()}

        # 2. Stale-koncept (stale_after passerad, ej superseded) — EN fråga
        self.env.cr.execute("""
            SELECT artifact_type_id, count(*)
            FROM ai_okf_concept
            WHERE artifact_type_id IN %s
              AND stale_after IS NOT NULL
              AND stale_after < now()
              AND status != 'superseded'
            GROUP BY artifact_type_id
        """, (type_ids,))
        stale_counts = dict(self.env.cr.fetchall())

        # 3. Storlek (opt-in via ir.config_parameter) — pg_column_size på embedding
        show_size = self.env['ir.config_parameter'].sudo().get_param(
            'ai_agent_core.dashboard_size_estimate', 'False') == 'True'
        size_map = {}
        if show_size:
            self.env.cr.execute("""
                SELECT artifact_type_id, sum(pg_column_size(embedding))
                FROM ai_okf_concept
                WHERE embedding IS NOT NULL AND artifact_type_id IN %s
                GROUP BY artifact_type_id
            """, (type_ids,))
            size_map = dict(self.env.cr.fetchall())

        # 4. Company memory-crons (enkelt sök — få rader)
        crons = self.env['ir.cron'].search(
            [('cron_name', 'ilike', 'Company Memory%')])
        crons_by_type = {}
        for t in types:
            keywords = _TYPE_CRON_KEYWORDS.get(
                t.name, [t.name])
            crons_by_type[t.id] = [
                c for c in crons
                if any(kw in (c.cron_name or '').lower()
                       for kw in keywords)]

        # 5. AGE-noder per label (EN cypher-fråga om graph aktiv)
        label_counts = {}
        try:
            executor = self.env['graph.executor'].sudo()
            res = executor.cypher(
                "MATCH (n) UNWIND labels(n) AS lbl "
                "RETURN lbl, count(*) AS cnt",
                read_only=True)
            label_counts = {lbl: cnt for lbl, cnt in res}
        except Exception:
            label_counts = {}
        graph_defs = {
            d.model_id.id: d.graph_label
            for d in self.env['graph.node.definition'].search([])
            if d.model_id
        }

        for rec in self:
            lines = []
            for t in types:
                total = stats.get(t.id, {}).get('total', 0)
                dirty = stats.get(t.id, {}).get('dirty', 0)
                superseded = stats.get(t.id, {}).get('superseded', 0)
                stale = stale_counts.get(t.id, 0)

                type_crons = crons_by_type.get(t.id, [])
                failures = sum(c.failure_count or 0 for c in type_crons)
                last_calls = [c.lastcall for c in type_crons if c.lastcall]
                last_run = max(last_calls) if last_calls else False

                label = graph_defs.get(t.model_id.id)
                node_count = label_counts.get(label, 0) if label else 0

                # Hälsa (spec odoo-mind-dashboard)
                if failures:
                    health, reason = 'red', \
                        '%d misslyckade cron-körningar' % failures
                elif t.model_id and total == 0:
                    health, reason = 'red', \
                        'bridge aktiv men 0 koncept'
                elif type_crons and not last_run:
                    health, reason = 'yellow', 'indexer-cron ej kört'
                elif dirty or stale:
                    bits = []
                    if dirty:
                        bits.append('%d dirty' % dirty)
                    if stale:
                        bits.append('%d stale' % stale)
                    health, reason = 'yellow', '; '.join(bits)
                else:
                    health, reason = 'green', 'OK'

                size = '—'
                if show_size:
                    bytes_ = size_map.get(t.id, 0)
                    size = '%.1f MB' % (bytes_ / (1024.0 * 1024.0)) \
                        if bytes_ else '—'

                lines.append((0, 0, {
                    'artifact_type_id': t.id,
                    'name': t.name,
                    'kind': t.kind,
                    'bridge_module': t.bridge_module or '',
                    'health': health,
                    'health_reason': reason,
                    'concept_count': total,
                    'dirty_count': dirty,
                    'superseded_count': superseded,
                    'stale_count': stale,
                    'node_count': node_count,
                    'size_estimate': size,
                    'indexer_tasks': '\n'.join(
                        c.cron_name for c in type_crons) or '—',
                    'last_run': last_run or False,
                    'cron_failures': failures,
                }))
            rec.line_ids = [(5, 0, 0)] + lines

    @api.depends()
    def _compute_stats(self):
        Concept = self.env['ai.okf.concept']
        for rec in self:
            rec.concept_count = Concept.search_count([])
            rec.company_concept_count = Concept.search_count(
                [('scope', '=', 'company')])
            rec.personal_concept_count = Concept.search_count(
                [('scope', '=', 'personal')])
            rec.coworker_concept_count = Concept.search_count(
                [('scope', '=', 'coworker')])
            rec.dirty_count = Concept.search_count([('dirty', '=', True)])
            rec.superseded_count = Concept.search_count(
                [('status', '=', 'superseded')])
            rec.memory_count = self.env['ai.memory'].search_count([])

            # Graph-status
            try:
                executor = self.env['graph.executor'].sudo()
                result = executor.cypher(
                    "MATCH (n) RETURN count(n) AS cnt", read_only=True)
                cnt = result[0][0] if result else '?'
                rec.graph_status = '✅ Aktiv'
                rec.graph_version = 'AGE %s' % cnt
            except Exception:
                rec.graph_status = '⬜ Ej aktiv'
                rec.graph_version = ''

            # Cron-status (OKF-indexerare + övriga AI-crons)
            lines = []
            crons = self.env['ir.cron'].search(
                [('cron_name', 'like', 'OKF%')])
            if not crons:
                crons = self.env['ir.cron'].search(
                    [('code', 'ilike', '_okf_cron_index_dirty')])
            for cron in crons:
                failures = cron.failure_count or 0
                mark = ' ❌' if failures else ''
                lines.append('[%s] %s — varje %s %s%s' % (
                    'x' if cron.active else ' ',
                    cron.cron_name or cron.code[:50],
                    cron.interval_number, cron.interval_type, mark))
            rec.cron_status = '\n'.join(lines) if lines else 'Inga OKF-crons'

    # ════════════════════════════════════════════
    # Manuella körningar (task 5.3)
    # ════════════════════════════════════════════
    def _run_okf_index(self, dirty_scope=None, artifact_types=None,
                       limit=200):
        """Kör _okf_upsert() för dirty-artefakter med filter.

        dirty_scope: None=alla, 'month'=ändrade denna månad,
        'today'=ändrade idag. artifact_types: m2m-filter.
        """
        domain = [('okf_dirty', '=', True)]
        if dirty_scope == 'month':
            since = datetime.now() - timedelta(days=30)
            domain.append(('write_date', '>=', since))
        elif dirty_scope == 'today':
            since = datetime.now().replace(hour=0, minute=0, second=0)
            domain.append(('write_date', '>=', since))
        if artifact_types:
            domain.append(('artifact_type_id', 'in', artifact_types.ids))

        Memory = self.env['ai.memory']
        # ai.memory har en FAISS-hjälpmetod som skuggar ORM:ts search
        dirty_ids = Memory._search(domain, limit=limit)
        dirty = Memory.browse(dirty_ids)
        if not dirty:
            self.run_result = 'Inga dirty-artefakter med detta filter.'
            return

        ok = self.env['ai.memory'].browse()
        for mem in dirty:
            try:
                concept = self.env['ai.okf.concept']._okf_upsert(
                    artifact_type=mem.artifact_type_id or 'learning',
                    concept_key='ai.memory,%s' % mem.id,
                    summary=mem.content or mem.name or '',
                    title=mem.name,
                    source_ref='ai.memory,%s' % mem.id,
                    owner_company_id=(not mem.quest_id and not mem.identity_id
                                      and self.env.company.id) or None,
                    owner_user_id=mem.identity_id.user_id.id
                    if mem.identity_id and mem.identity_id.user_id else None,
                    owner_coworker_id=mem.quest_id.id or None,
                    generated_by='dashboard',
                )
                if concept:
                    ok |= mem
            except Exception as e:
                _logger.warning('Dashboard run failed for memory %s: %s',
                                mem.id, e)
        if ok:
            ok.write({'okf_dirty': False})
        self.run_result = 'Indexerade %d/%d artefakter.' % (len(ok), len(dirty))

    def action_index_all(self):
        self._run_okf_index()
        return self._reopen()

    def action_index_month(self):
        self._run_okf_index(dirty_scope='month')
        return self._reopen()

    def action_index_today(self):
        self._run_okf_index(dirty_scope='today')
        return self._reopen()

    def action_index_category(self):
        self._run_okf_index(artifact_types=self.artifact_type_ids)
        return self._reopen()

    def action_migrate_legacy(self):
        """Kör legacy-migreringen (tasks 6.1–6.5)."""
        self.run_result = self.env['ai.okf.concept'].action_migrate_legacy()
        return self._reopen()

    def _reopen(self):
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'view_mode': 'form',
            'view_id': self.env.ref(
                'ai_agent_core.view_ai_okf_dashboard_form').id,
            'target': 'current',
            'res_id': self.id,
        }
