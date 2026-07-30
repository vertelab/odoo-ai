# -*- coding: utf-8 -*-
"""res.config.settings — AI Orkestrering configuration."""

from odoo import models, fields, api
import logging

_logger = logging.getLogger(__name__)


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    ai_api_secret = fields.Char(
        'AI API Secret',
        config_parameter='ai_agent_core.api_secret',
        help='Shared Bearer token for the /ai/v1/* (OpenAI-compatible) and '
             '/pi/callback endpoints. If empty, falls back to the '
             'AI_AGENT_API_SECRET environment variable.')

    # ── NATS Executor settings (tool-executor-nats) ──
    pi_nats_max_retries = fields.Integer(
        'Pi-Agent Max Retries',
        config_parameter='pi.nats.max_retries', default=3,
        help='Number of times to retry a NATS tool call before giving up.')

    pi_nats_max_restarts = fields.Integer(
        'Pi-Agent Max Restarts',
        config_parameter='pi.nats.max_restarts', default=5,
        help='Maximum number of Pi-agent restarts within the restart window.')

    pi_nats_restart_window = fields.Integer(
        'Pi-Agent Restart Window (minutes)',
        config_parameter='pi.nats.restart_window', default=60,
        help='Time window in minutes for counting Pi-agent restarts.')

    # ── AI Agent settings ──
    ai_agent_max_rounds = fields.Integer(
        'Max Loop Turns',
        config_parameter='ai_agent_core.max_rounds', default=20,
        help='Maximum loop iterations per turn for AI agents.')

    ai_agent_tool_timeout = fields.Integer(
        'Tool Timeout (seconds)',
        config_parameter='ai_agent_core.tool_timeout', default=30,
        help='Default timeout for tool calls in seconds.')

    ai_agent_default_identity_id = fields.Many2one(
        'ai.identity',
        string='Default Identity',
        config_parameter='ai_agent_core.default_identity_id',
        help='Default identity template for new agents.')

    # ── AI API settings ──
    ai_api_default_provider_id = fields.Many2one(
        'ai.provider',
        string='Default Provider',
        config_parameter='ai_agent_core.default_provider_id',
        help='Default AI provider for new agents.')

    ai_api_default_model_id = fields.Many2one(
        'ai.model',
        string='Default Model',
        config_parameter='ai_agent_core.default_model_id',
        help='Default AI model for new agents.')

    # ── Odoo Mind Graph settings ──
    odoomind_sync_batch_size = fields.Integer(
        'Graph Sync Batch Size',
        config_parameter='odoomind.sync_batch_size', default=500,
        help='Number of records per batch when syncing to AGE graph.')

    # ── Readonly display fields (from res.company) ──
    company_mission = fields.Html(
        related='company_id.company_mission', readonly=True)
    company_values = fields.Html(
        related='company_id.company_values', readonly=True)
    company_mission_last_review = fields.Datetime(
        related='company_id.company_mission_last_review', readonly=True)
    company_values_last_review = fields.Datetime(
        related='company_id.company_values_last_review', readonly=True)
    company_website_url = fields.Char(
        related='company_id.partner_id.website', readonly=True,
        string='Webbplats')
    company_website_rag_last_index = fields.Datetime(
        related='company_id.website_rag_last_index', readonly=True)

    @api.model
    def get_values(self):
        res = super().get_values()
        return res

    def set_values(self):
        super().set_values()

    # ── Action: Index website RAG ──
    def action_index_website(self):
        company = self.env.company
        if not company.partner_id.website:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Ingen webbplats',
                    'message': 'Företaget har ingen webbplats konfigurerad på partnern.',
                    'sticky': False,
                    'type': 'warning',
                }
            }
        company._index_website()
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Webbplats indexerad',
                'message': 'Webbplatsen har crawlat och sparats som RAG.',
                'sticky': False,
                'type': 'success',
            }
        }

    # ── Action: Suggest mission/values from RAG ──
    def action_suggest_identity(self):
        company = self.env.company
        if not company.website_rag_attachment_id:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Ingen RAG',
                    'message': 'Indexera webbplatsen först innan du skapar förslag.',
                    'sticky': False,
                    'type': 'warning',
                }
            }
        company._suggest_identity()
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Förslag skapat',
                'message': 'Mission och Values har uppdaterats baserat på webbplatsinnehållet.',
                'sticky': False,
                'type': 'success',
            }
        }

    # ── Action: Sync graph now ──
    def action_sync_graph_now(self):
        env = self.env
        defn = env['graph.node.definition'].search([], limit=1)
        if defn:
            defn._sync_all()
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Graph synkad',
                'message': 'Odoo Mind Graph har synkroniserats.',
                'sticky': False,
                'type': 'success',
            }
        }
