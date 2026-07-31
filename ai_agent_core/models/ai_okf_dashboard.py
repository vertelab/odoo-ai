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
