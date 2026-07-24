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
