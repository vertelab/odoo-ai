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
    3. If show_in_chat=False: denied (unless admin)
    4. If group_ids is set: user must be in at least one group
    5. If user_ids is set: user must be in user_ids list
    6. Both empty = open access
    """
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
    ('manual', 'Manual'),
    ('chat', 'Chat with User'),
    ('channel', 'Chat with Channel'),
    ('cron', 'Scheduled Action'),
    ('server-action', 'Server Action'),
    ('mail', 'Mail'),
]


class AIQuest(models.Model):
    _name = 'ai.quest'
    _description = 'AI Quest'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'sequence asc, name asc'

    name = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    description = fields.Text('Description', help='System prompt / quest purpose')
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

    # Access Control (quest-access-control change)
    show_in_chat = fields.Boolean(
        'Show in Web Chat', default=True,
        help='When enabled, this quest appears in the /ai/chat web interface. '
             'Disable to hide administrative or internal-only quests.',
    )
    group_ids = fields.Many2many(
        'res.groups', 'ai_quest_group_rel',
        'quest_id', 'group_id',
        string='Access Groups',
        help='Only users in these groups can see this quest in the web chat. '
             'Leave empty to allow all groups.',
    )
    user_ids = fields.Many2many(
        'res.users', 'ai_quest_user_rel',
        'quest_id', 'user_id',
        string='Access Users',
        help='Only these specific users can see this quest in the web chat. '
             'Leave empty to allow all users.',
    )

    # Mail

    # Stats
    session_count = fields.Integer(compute='_compute_session_count')
    session_ids = fields.One2many('ai.quest.session', 'quest_id')
    last_run = fields.Datetime()

    # Tags
    tag_ids = fields.Many2many('product.tag', string='Tags')

    @api.depends('agent_ids')
    def _compute_agent_count(self):
        for r in self:
            r.agent_count = len(r.agent_ids)

    def _compute_session_count(self):
        for r in self:
            r.session_count = len(r.session_ids)

    # -- Actions --
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


class AIQuestRun(models.TransientModel):
    """One-shot wizard that runs a quest using the core loop.

    This bridges the old ai_agent (LangGraph) and the new ai_agent_core.
    When use_core_loop=True on a quest, its run method delegates here.
    """
    _name = 'ai.quest.run'
    _description = 'Run AI Quest (Core Loop)'

    quest_id = fields.Many2one('ai.quest', required=True, string='Quest')
    prompt = fields.Text('Prompt', required=True)
    result = fields.Text('Result', readonly=True)
    status = fields.Selection([
        ('running', 'Running'),
        ('done', 'Done'),
        ('error', 'Error'),
    ], default='running')
    token_input = fields.Integer('Input Tokens', readonly=True)
    token_output = fields.Integer('Output Tokens', readonly=True)

    def action_run(self):
        """Run the quest via AgentLoop (synchronous wrapper)."""
        self.ensure_one()
        quest = self.quest_id

        import asyncio
        from ..core.provider import BifrostProvider
        from ..core.tools import ToolRegistry, builtin_tools
        from ..core.loop import AgentLoop, AgentConfig

        # Build provider from quest config
        agent = quest.agent_ids[:1].agent_id if quest.agent_ids else None
        model_name = "cerebras/gpt-oss-120b"  # default
        if agent:
            if agent.provider_type == 'bifrost':
                provider = BifrostProvider(
                    virtual_key=agent.bifrost_virtual_key or 'opencode',
                )
                model_name = agent.bifrost_model or model_name
            else:
                from ..core.provider import DirectProvider, Provider
                provider = DirectProvider(
                    provider=Provider(agent.direct_provider or 'openai'),
                )
                model_name = agent.direct_model or model_name
        else:
            provider = BifrostProvider()

        # Build tools
        tools = ToolRegistry()
        tools.register_many(builtin_tools())

        # Build config
        config = AgentConfig(
            model=model_name,
            system_prompt=quest.description or '',
            max_tokens=agent.max_tokens if agent else 4096,
            max_rounds=agent.max_rounds if agent else 20,
        )

        loop = AgentLoop(provider=provider, tools=tools, config=config)

        try:
            result = asyncio.run(loop.run(self.prompt))
            self.result = result.text
            self.status = 'done'
            self.token_input = result.input_tokens
            self.token_output = result.output_tokens

            # Create session record
            session = self.env['ai.quest.session'].create({
                'quest_id': quest.id,
                'agent_id': agent.id if agent else False,
                'identity_id': quest.identity_id.id if quest.identity_id else False,
                'status': 'done',
                'token_input': result.input_tokens,
                'token_output': result.output_tokens,
                'finish_reason': result.finish_reason,
            })

            quest.last_run = fields.Datetime.now()
            if quest.status == 'draft':
                quest.status = 'active'

        except Exception as e:
            self.status = 'error'
            self.result = f"Error: {e}"
            _logger.error("Quest run failed: %s", e, exc_info=True)

        return {
            'type': 'ir.actions.act_window',
            'res_model': 'ai.quest.run',
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }
