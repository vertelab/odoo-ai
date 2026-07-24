# -*- coding: utf-8 -*-
"""ai.quest — standalone, no LangGraph. Uses AgentLoop."""

import logging
from odoo import models, fields, api

_logger = logging.getLogger(__name__)

INIT_TYPES = [
    ('manual', 'Manual'), ('chat', 'Chat with User'), ('channel', 'Chat with Channel'),
    ('cron', 'Scheduled Action'), ('server-action', 'Server Action'), ('mail', 'Mail'),
]


class AIQuest(models.Model):
    _name = 'ai.quest'
    _description = 'AI Quest'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'sequence asc, name asc'

    name = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    description = fields.Text(help='System prompt / quest purpose')
    sub_description = fields.Char('Short Description')
    active = fields.Boolean(default=True)
    color = fields.Integer(default=lambda self: __import__('random').randint(1, 11))

    status = fields.Selection([
        ('draft', 'Draft'), ('active', 'Active'), ('done', 'Done'), ('error', 'Error'),
    ], default='draft')

    init_type = fields.Selection(INIT_TYPES, required=True, default='manual')
    model_id = fields.Many2one('ir.model', string='Target Model')
    model_name = fields.Char(related='model_id.model', readonly=True, store=True)
    filter_domain = fields.Char('Record Filter')

    agent_ids = fields.One2many('ai.quest.agent', 'quest_id', string='Agents')
    agent_count = fields.Integer(compute='_compute_agent_count')
    is_supervisor = fields.Boolean('Supervisor Mode')

    identity_id = fields.Many2one('ai.identity', string='Agent Identity')
    cron_id = fields.Many2one('ir.cron', string='Scheduled Action', ondelete='cascade')
    server_action_id = fields.Many2one('ir.actions.server', string='Server Action', ondelete='cascade')

    channel_id = fields.Many2one('discuss.channel', string='Channel')
    chat_user_id = fields.Many2one('res.users', string='Chat Bot User', readonly=True)
    allow_trigger_words = fields.Boolean('Use Activation Words')
    chat_trigger_words = fields.Text('Activation Words')

    use_chat_history = fields.Boolean(default=True)
    use_company_info = fields.Boolean(default=True)
    use_personal_info = fields.Boolean(default=True)
    use_personal_lang = fields.Boolean(default=True)
    use_time_context = fields.Boolean(default=True)
    chat_history_limit = fields.Integer(default=10)
    debug = fields.Boolean('Debug Mode')

    user_id = fields.Many2one('res.users', default=lambda self: self.env.user)
    company_id = fields.Many2one('res.company', default=lambda self: self.env.company)
    is_favorite = fields.Boolean('Favorite')

    session_count = fields.Integer(compute='_compute_session_count')
    session_ids = fields.One2many('ai.quest.session', 'quest_id')
    last_run = fields.Datetime()

    tag_ids = fields.Many2many('product.tag', string='Tags')

    @api.depends('agent_ids')
    def _compute_agent_count(self):
        for r in self:
            r.agent_count = len(r.agent_ids)

    def _compute_session_count(self):
        for r in self:
            r.session_count = len(r.session_ids)

    def action_get_agents(self):
        return {
            'name': 'Agents', 'type': 'ir.actions.act_window',
            'res_model': 'ai.agent', 'view_mode': 'kanban,list,form',
            'target': 'current',
            'domain': [('id', 'in', self.agent_ids.mapped('agent_id').ids)],
        }

    def action_get_sessions(self):
        return {
            'name': 'Sessions', 'type': 'ir.actions.act_window',
            'res_model': 'ai.quest.session', 'view_mode': 'list,form',
            'target': 'current',
            'domain': [('quest_id', '=', self.id)],
        }


class AIQuestAgent(models.Model):
    _name = 'ai.quest.agent'
    _description = 'Quest Agent Assignment'
    _order = 'sequence asc'

    quest_id = fields.Many2one('ai.quest', required=True, ondelete='cascade')
    agent_id = fields.Many2one('ai.agent', required=True, string='Agent')
    sequence = fields.Integer(default=10)
