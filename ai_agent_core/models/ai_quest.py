# -*- coding: utf-8 -*-
"""
ai.quest — standalone, no LangGraph. Uses AgentLoop.
Model name matches ai_agent for drop-in replacement.
"""

import json, logging, re, uuid
from odoo import models, fields, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


def _quest_is_accessible(quest, user):
    """Check if a user can access a quest via the web chat.

    Access logic:
    1. Admin users (base.group_system) always have access
    2. Quest owner (user_id) always has access
    sub_description = fields.Char('Short Description')
    active = fields.Boolean(default=True)
    color = fields.Integer(default=lambda self: __import__('random').randint(1, 11))

    # Status
    status = fields.Selection([
        ('draft', 'Draft'), ('active', 'Active'),
        ('done', 'Done'), ('error', 'Error'),
    ], default='draft')

    # Init
    init_type = fields.Selection(INIT_TYPES, required=True, default='manual')
    model_id = fields.Many2one('ir.model', string='Target Model')
    model_name = fields.Char(related='model_id.model', readonly=True, store=True)
    filter_domain = fields.Char('Record Filter')

    # Agents (replaces LangGraph supervisor pattern)
    agent_ids = fields.One2many('ai.quest.agent', 'quest_id', string='Agents')
    agent_count = fields.Integer(compute='_compute_agent_count')
    is_supervisor = fields.Boolean('Supervisor Mode',
        help='Route to specialist agents instead of running directly')

    # Identity
    identity_id = fields.Many2one('ai.identity', string='Agent Identity')

    # Schedule
    cron_id = fields.Many2one('ir.cron', string='Scheduled Action', ondelete='cascade')
    server_action_id = fields.Many2one('ir.actions.server', string='Server Action',
                                        ondelete='cascade')

    # Chat
    channel_id = fields.Many2one('discuss.channel', string='Channel')
    chat_user_id = fields.Many2one('res.users', string='Chat Bot User', readonly=True)
    allow_trigger_words = fields.Boolean('Use Activation Words')
    chat_trigger_words = fields.Text('Activation Words', help='Comma-separated')

    # Config
    use_chat_history = fields.Boolean(default=True)
    use_company_info = fields.Boolean(default=True)
    use_personal_info = fields.Boolean(default=True)
    use_personal_lang = fields.Boolean(default=True)
    use_time_context = fields.Boolean(default=True)
    chat_history_limit = fields.Integer(default=10)
    debug = fields.Boolean('Debug Mode')
    use_core_loop = fields.Boolean(
        'Use Core Loop',
        default=False,
        help='Use the new AgentLoop (ai_agent_core) instead of LangGraph. '
             'Enable to migrate from the old ai_agent module.',
    )

    # Owner
    user_id = fields.Many2one('res.users', default=lambda self: self.env.user)
    company_id = fields.Many2one('res.company', default=lambda self: self.env.company)
    is_favorite = fields.Boolean('Favorite')

