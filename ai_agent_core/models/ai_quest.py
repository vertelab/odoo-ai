# -*- coding: utf-8 -*-
"""ai.quest — standalone, no LangGraph. Uses AgentLoop."""

import json, logging, re, uuid
from odoo import models, fields, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


def _quest_is_accessible(quest, user):
    """Check if a user can access a quest via the web chat."""
    if user.has_group('base.group_system'):
        return True
    if quest.user_id and quest.user_id.id == user.id:
        return True
    if not quest.show_in_chat:
        return False
    if quest.group_ids:
        user_grp = set(user.groups_id.ids)
        quest_grp = set(quest.group_ids.ids)
        if not (user_grp & quest_grp):
            return False
    if quest.user_ids:
        if user.id not in quest.user_ids.ids:
            return False
    return True


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
    model_ids = fields.Many2many('ir.model', 'ai_quest_model_rel',
        'quest_id', 'model_id', string='Target Models',
        help='Models this quest can work with')
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

    # Access Control (quest-access-control)
    show_in_chat = fields.Boolean('Show in Web Chat', default=True)
    group_ids = fields.Many2many('res.groups', 'ai_quest_group_rel', 'quest_id', 'group_id', string='Access Groups')
    user_ids = fields.Many2many('res.users', 'ai_quest_user_rel', 'quest_id', 'user_id', string='Access Users')

    # Core loop migration
    use_core_loop = fields.Boolean('Use Core Loop', default=False)

    session_count = fields.Integer(compute='_compute_session_count')
    session_ids = fields.One2many('ai.quest.session', 'quest_id')

    # Systemtoken tracking
    session_line_ids = fields.One2many(
        'ai.quest.session.line', related='session_ids.session_line_ids',
        string='Session Lines',
        help='All message lines from all sessions of this quest')
    session_line_count = fields.Integer(
        'Systemtokens (månad)', compute='_compute_session_line_count',
        help='Total systemtokens consumed this calendar month')
    started_mtokens = fields.Integer(
        'Påbörjade M-tokens', compute='_compute_started_mtokens',
        help='ceil(session_line_count / 1_000_000) — for billing')

    # Cap enforcement (Horisont 2)
    monthly_cap_mtokens = fields.Integer(
        'Månadstak (M systemtokens)', default=0,
        help='0 = unlimited. Cap in millions of systemtokens.')
    cap_warning_sent = fields.Boolean('Varning skickad')
    cap_exhausted = fields.Boolean('Tak överskridet')

    skill_copy_ids = fields.One2many('ai.quest.skill', 'quest_id',
        string='Skill Copies',
        help='Quest-specific copies of shared skills')
    last_run = fields.Datetime()

    tag_ids = fields.Many2many('product.tag', string='Tags')

    @api.depends('agent_ids')
    def _compute_agent_count(self):
        for r in self:
            r.agent_count = len(r.agent_ids)

    def _compute_session_count(self):
        for r in self:
            r.session_count = len(r.session_ids)

    @api.depends('session_line_ids.token_sys', 'session_line_ids.create_date')
    def _compute_session_line_count(self):
        """Sum of systemtokens for the current calendar month."""
        from datetime import date
        today = date.today()
        month_start = date(today.year, today.month, 1)
        for r in self:
            total = 0
            for line in r.session_line_ids:
                if line.create_date and line.create_date.date() >= month_start:
                    total += line.token_sys or 0
            r.session_line_count = total

    @api.depends('session_line_count')
    def _compute_started_mtokens(self):
        """Number of started millions (rounded up)."""
        import math
        for r in self:
            r.started_mtokens = math.ceil(r.session_line_count / 1_000_000) if r.session_line_count else 0

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
