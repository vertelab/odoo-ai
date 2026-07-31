# -*- coding: utf-8 -*-
"""res.config.settings — AI Orkestrering configuration."""

from odoo import models, fields, api
import logging

_logger = logging.getLogger(__name__)

BG_CRON_NAMES = [
    ('memory_consolidation', 'AI: Daglig minneskonsolidering'),
    ('monthly_summary', 'AI: Generera månadssammanställning'),
    ('onboard', 'AI: ONBOARD — scanna efter quest-kandidater'),
    ('scheduled_quests', 'AI: Run Scheduled Quests'),
    ('bifrost_sync', 'AI: Synca modeller från Bifrost'),
    ('kaizen', 'AI: Veckovis Kaizen-rapport'),
    ('website_rag', 'Website RAG Refresh'),
    ('graph_sync', 'Odoo Mind Graph Sync'),
]


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    # ─────────────────────────────────────────────
    # AI API
    # ─────────────────────────────────────────────
    ai_api_secret = fields.Char(
        'AI API Secret',
        config_parameter='ai_agent_core.api_secret',
        help='Shared Bearer token for the /ai/v1/* (OpenAI-compatible) and '
             '/pi/callback endpoints. If empty, falls back to the '
             'AI_AGENT_API_SECRET environment variable.')

    ai_api_default_provider_id = fields.Many2one(
        'ai.provider', string='Default Provider',
        config_parameter='ai_agent_core.default_provider_id',
        help='Default AI provider for new agents.')

    ai_api_default_model_id = fields.Many2one(
        'ai.model', string='Default Model',
        config_parameter='ai_agent_core.default_model_id',
        help='Default AI model for new agents.')

    # ─────────────────────────────────────────────
    # Tool Executor (NATS)
    # ─────────────────────────────────────────────
    pi_nats_max_retries = fields.Integer(
        'Pi-Agent Max Retries', default=3,
        config_parameter='pi.nats.max_retries',
        help='Number of times to retry a NATS tool call before giving up.')

    pi_nats_max_restarts = fields.Integer(
        'Pi-Agent Max Restarts', default=5,
        config_parameter='pi.nats.max_restarts',
        help='Maximum number of Pi-agent restarts within the restart window.')

    pi_nats_restart_window = fields.Integer(
        'Pi-Agent Restart Window (minutes)', default=60,
        config_parameter='pi.nats.restart_window',
        help='Time window in minutes for counting Pi-agent restarts.')

    # ─────────────────────────────────────────────
    # AI Agents
    # ─────────────────────────────────────────────
    ai_agent_max_rounds = fields.Integer(
        'Max Loop Turns', default=20,
        config_parameter='ai_agent_core.max_rounds',
        help='Maximum loop iterations per turn for AI agents.')

    ai_agent_tool_timeout = fields.Integer(
        'Tool Timeout (seconds)', default=30,
        config_parameter='ai_agent_core.tool_timeout',
        help='Default timeout for tool calls in seconds.')

    ai_agent_default_identity_id = fields.Many2one(
        'ai.identity', string='Default Identity',
        config_parameter='ai_agent_core.default_identity_id',
        help='Default identity template for new agents.')

    # ─────────────────────────────────────────────
    # Odoo Mind
    # ─────────────────────────────────────────────
    odoomind_sync_batch_size = fields.Integer(
        'Graph Sync Batch Size', default=500,
        config_parameter='odoomind.sync_batch_size',
        help='Number of records per batch when syncing to AGE graph.')

    graph_node_count = fields.Char(
        'Graph Node Count', compute='_compute_graph_node_count', readonly=True)

    @api.depends()
    def _compute_graph_node_count(self):
        for r in self:
            try:
                executor = self.env['graph.executor'].sudo()
                result = executor.cypher(
                    "MATCH (n) RETURN count(n) AS cnt", read_only=True)
                cnt = result[0][0] if result else '?'
                r.graph_node_count = str(cnt)
            except Exception:
                r.graph_node_count = 'N/A'

    # ── Company / Personal / Coworker Memory settings ──
    company_memory_auto_extract = fields.Boolean(
        'Auto-extraction from Discuss', default=True,
        config_parameter='odoomind.company_memory.auto_extract',
        help='Automatically extract company memories from Discuss conversations.')

    company_memory_consolidation_days = fields.Integer(
        'Consolidation Interval (days)', default=1,
        config_parameter='odoomind.company_memory.consolidation_days',
        help='How often to consolidate company memories.')

    personal_memory_learning_enabled = fields.Boolean(
        'Learning from Discuss', default=True,
        config_parameter='odoomind.personal_memory.learning_enabled',
        help='Enable personal memory learning from Discuss conversations.')

    personal_memory_auto_nudge = fields.Boolean(
        'Auto-nudge goals', default=True,
        config_parameter='odoomind.personal_memory.auto_nudge',
        help='Automatically nudge users towards their personal goals.')

    personal_memory_nudge_interval = fields.Integer(
        'Nudge Interval (days)', default=7,
        config_parameter='odoomind.personal_memory.nudge_interval',
        help='How often to send nudges for personal goals.')

    personal_memory_consolidation_hours = fields.Integer(
        'Consolidation Interval (hours)', default=24,
        config_parameter='odoomind.personal_memory.consolidation_hours',
        help='How often to consolidate personal memories.')

    coworker_memory_retention = fields.Boolean(
        'Session Memory Retention', default=True,
        config_parameter='odoomind.coworker_memory.retention_enabled',
        help='Enable memory retention for AI coworkers across sessions.')

    coworker_memory_retention_limit = fields.Integer(
        'Retention Limit (sessions)', default=100,
        config_parameter='odoomind.coworker_memory.retention_limit',
        help='Maximum number of sessions to retain per coworker.')

    coworker_memory_forget_days = fields.Integer(
        'Forget After (days)', default=90,
        config_parameter='odoomind.coworker_memory.forget_days',
        help='After how many days to forget old coworker sessions.')

    # ── Företagsidentitet (editable) ──
    company_website_url_edit = fields.Char(
        'Webbplats URL',
        help='Company website URL. Saved to res.partner.website.')

    # Readonly display fields
    company_mission = fields.Html(
        related='company_id.company_mission', readonly=True)
    company_values = fields.Html(
        related='company_id.company_values', readonly=True)
    company_mission_last_review = fields.Datetime(
        related='company_id.company_mission_last_review', readonly=True)
    company_values_last_review = fields.Datetime(
        related='company_id.company_values_last_review', readonly=True)
    company_website_rag_last_index = fields.Datetime(
        related='company_id.website_rag_last_index', readonly=True)

    # ─────────────────────────────────────────────
    # Heartbeat Settings
    # ─────────────────────────────────────────────
    heartbeat_enabled = fields.Boolean(
        'Heartbeat Active', default=True,
        help='Enable the AI heartbeat system that wakes coworkers '
             'periodically to check for work.')
    heartbeat_interval = fields.Integer(
        'Heartbeat Interval (minutes)', default=5,
        help='How often each active coworker wakes to check budget, '
             'tasks, goals, and nudge needs.')

    # ─────────────────────────────────────────────
    # Background Jobs — dynamic fields per cron
    # ─────────────────────────────────────────────
    bg_status_summary = fields.Text(
        'Background Jobs Status', compute='_compute_bg_status', readonly=True)

    @api.depends()
    def _compute_bg_status(self):
        """Build a human-readable status summary of all AI crons."""
        for r in self:
            lines = []
            cron_names = [c[1] for c in BG_CRON_NAMES]
            crons = self.env['ir.cron'].search([('cron_name', 'in', cron_names)])
            for cron in crons:
                failures = cron.failure_count or 0
                fail_mark = ' ❌' if failures > 0 else ''
                lines.append(
                    f"[{'x' if cron.active else ' '}] {cron.cron_name}"
                    f" — varje {cron.interval_number} {cron.interval_type}"
                    f"{fail_mark} ({failures} fel)"
                )
            r.bg_status_summary = '\n'.join(lines) if lines else 'Inga cron-jobb hittades'

    @api.model
    def get_values(self):
        res = super().get_values()
        company = self.env.company

        # Website URL from partner
        res['company_website_url_edit'] = company.partner_id.website or ''

        # Compute graph node count
        try:
            executor = self.env['graph.executor'].sudo()
            result = executor.cypher(
                "MATCH (n) RETURN count(n) AS cnt", read_only=True)
            cnt = result[0][0] if result else '?'
            res['graph_node_count'] = str(cnt)
        except Exception:
            res['graph_node_count'] = 'N/A'

        # Background jobs status
        get_param = self.env['ir.config_parameter'].sudo().get_param
        lines = []
        cron_names = [c[1] for c in BG_CRON_NAMES]
        crons = self.env['ir.cron'].search([('cron_name', 'in', cron_names)])
        for cron in crons:
            failures = cron.failure_count or 0
            fail_mark = ' ❌' if failures > 0 else ''
            lines.append(
                f"[{'x' if cron.active else ' '}] {cron.cron_name}"
                f" — varje {cron.interval_number} {cron.interval_type}"
                f"{fail_mark} ({failures} fel)")
        res['bg_status_summary'] = '\n'.join(lines) if lines else 'Inga cron-jobb hittades'

        # Auto-read config_parameter for new fields
        res['company_memory_auto_extract'] = get_param(
            'odoomind.company_memory.auto_extract', 'True') == 'True'
        res['company_memory_consolidation_days'] = int(get_param(
            'odoomind.company_memory.consolidation_days', '1'))
        res['personal_memory_learning_enabled'] = get_param(
            'odoomind.personal_memory.learning_enabled', 'True') == 'True'
        res['personal_memory_auto_nudge'] = get_param(
            'odoomind.personal_memory.auto_nudge', 'True') == 'True'
        res['personal_memory_nudge_interval'] = int(get_param(
            'odoomind.personal_memory.nudge_interval', '7'))
        res['personal_memory_consolidation_hours'] = int(get_param(
            'odoomind.personal_memory.consolidation_hours', '24'))
        res['coworker_memory_retention'] = get_param(
            'odoomind.coworker_memory.retention_enabled', 'True') == 'True'
        res['coworker_memory_retention_limit'] = int(get_param(
            'odoomind.coworker_memory.retention_limit', '100'))
        res['coworker_memory_forget_days'] = int(get_param(
            'odoomind.coworker_memory.forget_days', '90'))
        res['heartbeat_enabled'] = get_param(
            'ai_agent_core.heartbeat_enabled', 'True') == 'True'
        res['heartbeat_interval'] = int(get_param(
            'ai_agent_core.heartbeat_interval', '5'))

        return res

    def set_values(self):
        super().set_values()
        company = self.env.company

        # Save website URL to partner
        if company.partner_id.website != self.company_website_url_edit:
            company.partner_id.sudo().write({
                'website': self.company_website_url_edit or False,
            })

        # Save new config_parameter fields
        set_param = self.env['ir.config_parameter'].sudo().set_param
        set_param('odoomind.company_memory.auto_extract',
                  str(self.company_memory_auto_extract))
        set_param('odoomind.company_memory.consolidation_days',
                  str(self.company_memory_consolidation_days))
        set_param('odoomind.personal_memory.learning_enabled',
                  str(self.personal_memory_learning_enabled))
        set_param('odoomind.personal_memory.auto_nudge',
                  str(self.personal_memory_auto_nudge))
        set_param('odoomind.personal_memory.nudge_interval',
                  str(self.personal_memory_nudge_interval))
        set_param('odoomind.personal_memory.consolidation_hours',
                  str(self.personal_memory_consolidation_hours))
        set_param('odoomind.coworker_memory.retention_enabled',
                  str(self.coworker_memory_retention))
        set_param('odoomind.coworker_memory.retention_limit',
                  str(self.coworker_memory_retention_limit))
        set_param('odoomind.coworker_memory.forget_days',
                  str(self.coworker_memory_forget_days))
        set_param('ai_agent_core.heartbeat_enabled',
                  str(self.heartbeat_enabled))
        set_param('ai_agent_core.heartbeat_interval',
                  str(self.heartbeat_interval))

    # ─────────────────────────────────────────────
    # Action methods
    # ─────────────────────────────────────────────

    def _notify(self, title, message, type='success'):
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': title,
                'message': message,
                'sticky': False,
                'type': type,
            }
        }

    def action_open_company(self):
        """Open the current company's form view."""
        company = self.env.company
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'res.company',
            'res_id': company.id,
            'views': [(False, 'form')],
            'view_mode': 'form',
            'target': 'current',
            'context': {'form_view_initial_mode': 'edit'},
        }

    def action_suggest_mission(self):
        """Suggest mission only from website RAG."""
        company = self.env.company
        if not company.website_rag_attachment_id:
            return self._notify(
                'Ingen RAG',
                'Indexera webbplatsen först innan du skapar förslag.',
                'warning')
        company._suggest_identity()
        return self._notify(
            'Mission uppdaterad',
            'Mission har uppdaterats baserat på webbplatsinnehållet.')

    def action_suggest_values(self):
        """Suggest values only from website RAG."""
        company = self.env.company
        if not company.website_rag_attachment_id:
            return self._notify(
                'Ingen RAG',
                'Indexera webbplatsen först innan du skapar förslag.',
                'warning')
        company._suggest_identity()
        return self._notify(
            'Values uppdaterade',
            'Values har uppdaterats baserat på webbplatsinnehållet.')

    def action_index_website(self):
        company = self.env.company
        if not company.partner_id.website:
            return self._notify(
                'Ingen webbplats',
                'Företaget har ingen webbplats konfigurerad på partnern.',
                'warning')
        company._index_website()
        return self._notify(
            'Webbplats indexerad',
            'Webbplatsen har crawlat och sparats som RAG.')

    def action_sync_graph_now(self):
        defn = self.env['graph.node.definition'].search([], limit=1)
        if defn:
            defn._sync_all()
        return self._notify(
            'Graph synkad',
            'Odoo Mind Graph har synkroniserats.')

    def action_toggle_cron(self, cron_key, active):
        """Toggle a cron job on/off by its key in BG_CRON_NAMES."""
        name = dict(BG_CRON_NAMES).get(cron_key)
        if not name:
            return False
        cron = self.env['ir.cron'].search([('cron_name', '=', name)], limit=1)
        if cron:
            cron.write({'active': active})
        return True
