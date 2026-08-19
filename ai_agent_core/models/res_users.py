# -*- coding: utf-8 -*-
"""res.users — personal AI companion (Hole 3)."""

from odoo import models, fields, api
import logging

_logger = logging.getLogger(__name__)


class ResUsers(models.Model):
    _inherit = 'res.users'

    personal_coworker_id = fields.Many2one(
        'ai.coworker', string='AI Companion',
        help='Personal AI quest for this user. Created automatically '
             'when personal companion is enabled.')

    # ── Personal Memory (ai.personal.memory) ──
    personal_memory_ids = fields.One2many(
        'ai.personal.memory', 'user_id',
        string='Personal Memories',
        help='All personal memories for this user. '
             'Accessible from ANY AI quest the user interacts with.')

    personal_memory_count = fields.Integer(
        string='Memory Count',
        compute='_compute_personal_memory_count',
        help='Number of personal memories for this user.')

    # ── Company Memory Access ──
    learn_from_discuss = fields.Boolean('Learn from Discuss', default=True, help='Extract learnings from Discuss channel conversations.')

    company_memory_categories = fields.Many2many(
        'ai.company.memory.category',
        'res_users_company_memory_category_rel',
        'user_id', 'category_id',
        string='Company Memory Categories',
        help='Additional company memory categories this user can access.\n'
             'By default, access is determined by the user\'s groups.\n'
             'Use this to grant extra access to specific categories.')

    @api.depends('personal_memory_ids')
    def _compute_personal_memory_count(self):
        for r in self:
            r.personal_memory_count = len(r.personal_memory_ids)

    # ── Personal Goals (ai.personal.goal) ──
    personal_goal_ids = fields.One2many(
        'ai.personal.goal', 'user_id',
        string='Personal Goals',
        help='Alla personliga mål för denna användare.')

    personal_goal_count = fields.Integer(
        string='Goal Count',
        compute='_compute_personal_goal_count',
        help='Antal personliga mål för denna användare.')

    @api.depends('personal_goal_ids')
    def _compute_personal_goal_count(self):
        for r in self:
            r.personal_goal_count = len(r.personal_goal_ids)

    def action_open_personal_goals(self):
        """Smartknapp: öppna användarens egna mål (Min Profil)."""
        self.ensure_one()
        return {
            'name': 'Personal Goals',
            'type': 'ir.actions.act_window',
            'res_model': 'ai.personal.goal',
            'view_mode': 'list,form',
            'views': [[False, 'list'], [False, 'form']],
            'target': 'current',
            'domain': [('user_id', '=', self.id)],
            'context': {
                'default_user_id': self.id,
            },
        }

    def action_open_personal_memory(self):
        """Smart button: öppna användarens personliga minnen."""
        self.ensure_one()
        return {
            'name': 'Personal Memories',
            'type': 'ir.actions.act_window',
            'res_model': 'ai.personal.memory',
            'view_mode': 'list,form',
            'views': [[False, 'list'], [False, 'form']],
            'target': 'current',
            'domain': [('user_id', '=', self.id)],
            'context': {
                'default_user_id': self.id,
            },
        }

    def _create_personal_companion(self, identity_template=None):
        """Create or get personal AI companion quest for this user."""
        self.ensure_one()
        if self.personal_coworker_id:
            return self.personal_coworker_id

        # Get identity template from system settings
        if not identity_template:
            enabled = self.env['ir.config_parameter'].sudo().get_param(
                'ai_agent_core.personal_companion_enabled', 'False')
            if enabled != 'True':
                return False
            template_id = self.env['ir.config_parameter'].sudo().get_param(
                'ai_agent_core.personal_companion_identity_id', '0')
            if template_id and template_id != '0':
                identity_template = self.env['ai.identity'].browse(int(template_id))

        # Create copied identity
        identity_copy = None
        if identity_template and identity_template.exists():
            identity_copy = identity_template.copy_for_user(self)

        # Create personal quest
        quest = self.env['ai.coworker'].create({
            'name': f"{self.name}'s AI Companion",
            'init_type': 'chat',
            'user_id': self.id,
            'show_in_chat': True,
            'identity_id': identity_copy.id if identity_copy else None,
            'description': f"Personal AI companion for {self.name}. "
                          f"Learns from interactions and adapts over time.",
            'status': 'active',
        })
        self.personal_coworker_id = quest.id
        _logger.info('Created personal AI companion for %s: quest %s',
                     self.name, quest.id)
        return quest

    # ════════════════════════════════════════════
    # Offboarding — rätten att bli glömd (task 7.12)
    # ════════════════════════════════════════════
    def action_offboard_archive_personal_memory(self):
        """Arkivera/radera användarens personliga koncept vid offboarding.

        Rätt att bli glömd realiseras nu (inte bara i GDPR-modulen).
        Company/coworker-koncept rörs inte — de tillhör företaget.
        """
        archived = self.env['ai.okf.concept'].search([
            ('owner_user_id', '=', self.id),
            ('archived', '=', False),
        ])
        if archived:
            archived.write({'archived': True})
            _logger.info('Offboarding: arkiverade %s personliga koncept '
                         'för användare %s', len(archived), self.id)
        return len(archived)
