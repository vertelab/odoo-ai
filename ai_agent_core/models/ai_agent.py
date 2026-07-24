# -*- coding: utf-8 -*-
"""ai.agent — standalone with identity, skills, tools, provider, budget."""

import logging
from odoo import models, fields, api

_logger = logging.getLogger(__name__)


class AIAgent(models.Model):
    _name = 'ai.agent'
    _description = 'AI Agent'
    _order = 'name asc'

    name = fields.Char(required=True)
    active = fields.Boolean(default=True)
    description = fields.Text()
    sequence = fields.Integer(default=10)

    # Identity
    identity_id = fields.Many2one('ai.identity', string='Identity',
                                   help='What personality/soul this agent has')

    # Skills
    skill_ids = fields.Many2many('ai.skill', 'ai_agent_skill_rel',
                                  'agent_id', 'skill_id', string='Skills')

    # Tools
    tool_ids = fields.One2many('ai.agent.tool', 'agent_id', string='Tools')

    # Provider & Model
    provider_type = fields.Selection([
        ('bifrost', 'Bifrost Gateway'),
        ('direct', 'Direct Provider'),
    ], default='bifrost')
    bifrost_virtual_key = fields.Selection([
        ('opencode', 'Opencode'), ('dina', 'Dina'), ('plastshop', 'Plastshop'),
    ], default='opencode')
    bifrost_model = fields.Char('Bifrost Model', default='cerebras/gpt-oss-120b')
    direct_provider = fields.Selection([
        ('openai', 'OpenAI'), ('anthropic', 'Anthropic'), ('deepseek', 'DeepSeek'),
    ])
    direct_model = fields.Char('Direct Model')

    # Config
    temperature = fields.Float(default=0.7)
    max_tokens = fields.Integer(default=4096)
    max_rounds = fields.Integer(default=10)

    # Budget
    budget_limit = fields.Float('Monthly Budget (USD)', default=0)
    budget_used = fields.Float('Used This Month', default=0)

    # Status
    status = fields.Selection([
        ('draft', 'Draft'), ('active', 'Active'), ('error', 'Error'),
    ], default='draft')
    last_run = fields.Datetime()

    # Stats
    quest_count = fields.Integer(compute='_compute_quest_count')

    def _compute_quest_count(self):
        for r in self:
            r.quest_count = self.env['ai.quest.agent'].search_count([
                ('agent_id', '=', r.id)
            ])

    def action_get_quests(self):
        quest_ids = self.env['ai.quest.agent'].search([
            ('agent_id', '=', self.id)
        ]).mapped('quest_id').ids
        return {
            'name': 'Quests', 'type': 'ir.actions.act_window',
            'res_model': 'ai.quest', 'view_mode': 'kanban,list,form',
            'target': 'current', 'domain': [('id', 'in', quest_ids)],
        }


class AIAgentTool(models.Model):
    _name = 'ai.agent.tool'
    _description = 'Agent Tool'
    _order = 'sequence asc'

    agent_id = fields.Many2one('ai.agent', required=True, ondelete='cascade')
    name = fields.Char('Tool Name', required=True)
    description = fields.Text('Description')
    parameters = fields.Text('Parameters (JSON Schema)')
    risk_level = fields.Selection([
        ('safe', 'Safe'), ('read_only', 'Read Only'),
        ('write', 'Write'), ('destructive', 'Destructive'),
    ], default='read_only')
    sequence = fields.Integer(default=10)
