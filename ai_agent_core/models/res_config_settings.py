# -*- coding: utf-8 -*-
"""res.config.settings — Personal AI companion toggle (Hole 3)."""

from odoo import models, fields, api
import logging

_logger = logging.getLogger(__name__)


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    ai_personal_companion_enabled = fields.Boolean(
        'Personal AI Companion',
        config_parameter='ai_agent_core.personal_companion_enabled',
        help='Create a personal AI quest for each user. '
             'The companion learns from interactions and adapts over time.')

    ai_personal_companion_identity_id = fields.Many2one(
        'ai.identity',
        string='Default Identity Template',
        config_parameter='ai_agent_core.personal_companion_identity_id',
        help='Identity template to copy for new personal companions. '
             'Each user gets their own copy that evolves independently.')

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

    # ── Odoo Mind Graph settings ──
    odoomind_sync_batch_size = fields.Integer(
        'Graph Sync Batch Size',
        config_parameter='odoomind.sync_batch_size', default=500,
        help='Number of records per batch when syncing to AGE graph.')

    @api.model
    def get_values(self):
        res = super().get_values()
        res['ai_personal_companion_enabled'] = self.env['ir.config_parameter'].sudo().get_param(
            'ai_agent_core.personal_companion_enabled', 'False') == 'True'
        template_id = self.env['ir.config_parameter'].sudo().get_param(
            'ai_agent_core.personal_companion_identity_id', '0')
        if template_id and template_id != '0':
            res['ai_personal_companion_identity_id'] = int(template_id)
        return res

    def set_values(self):
        super().set_values()
        self.env['ir.config_parameter'].sudo().set_param(
            'ai_agent_core.personal_companion_enabled',
            str(self.ai_personal_companion_enabled))
        self.env['ir.config_parameter'].sudo().set_param(
            'ai_agent_core.personal_companion_identity_id',
            str(self.ai_personal_companion_identity_id.id if self.ai_personal_companion_identity_id else '0'))

        # Create companions for existing users if enabled
        if self.ai_personal_companion_enabled:
            self._ensure_companions()

    def _ensure_companions(self):
        """Create personal companions for users who don't have one yet."""
        users = self.env['res.users'].search([
            ('personal_quest_id', '=', False),
            ('active', '=', True),
            ('share', '=', False),  # Not portal users
        ])
        template = self.ai_personal_companion_identity_id
        created = 0
        for user in users:
            try:
                user._create_personal_companion(template)
                created += 1
            except Exception as e:
                _logger.warning('Failed to create companion for %s: %s',
                              user.name, e)
        if created:
            _logger.info('Created %d personal AI companions', created)
