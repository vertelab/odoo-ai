# -*- coding: utf-8 -*-
"""ai.agent — standalone with identity, skills, tools, memories, provider, budget."""

import logging
from odoo import models, fields, api

_logger = logging.getLogger(__name__)


class AIAgent(models.Model):
    _name = 'ai.agent'
    _description = 'AI Agent'
    _order = 'name asc'

    name = fields.Char(required=True)
    active = fields.Boolean(default=True)
    description = fields.Text(help='What this agent does — used by supervisor router')
    sequence = fields.Integer(default=10)

    # Role/Goal/Backstory (for supervisor orchestration)
    ai_role = fields.Char('Role', help='e.g. "Financial Analyst", "Code Reviewer"')
    ai_goal = fields.Text('Goal', help='What this agent aims to accomplish')
    ai_backstory = fields.Text('Backstory', help='Background context for the agent')

    # Identity
    identity_id = fields.Many2one('ai.identity', string='Identity',
                                   help='What personality/soul this agent has')

    # Skills (task-specific — not pipeline/orchestration)
    skill_ids = fields.Many2many('ai.skill', 'ai_agent_skill_rel',
                                  'agent_id', 'skill_id', string='Skills',
                                  help='Task-specific skills this agent can perform')

    # Tools
    tool_ids = fields.One2many('ai.agent.tool', 'agent_id', string='Tools')

    # Memories (FAISS/pgvector RAG)
    memory_ids = fields.One2many('ai.agent.memory', 'agent_id', string='Memories',
                                  help='FAISS/pgvector knowledge bases available to this agent')

    # Model (points to ai.model — handles both Bifrost and Direct)
    model_id = fields.Many2one('ai.model', string='Model',
                                help='The LLM this agent uses. Can be Bifrost or Direct.')
    model_name = fields.Char(related='model_id.name', readonly=True, store=True)

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

    def get_agent_name(self):
        """Generate an informative label showing agent's role and capabilities.

        Used by supervisor routing to describe agents to the router LLM,
        and as future mermaid graph labels.

        Returns a multi-line string with role, tools, memories, and model.
        """
        import re
        name = re.sub(r'[()\[\]{}\}:]', ' ', (self.name or '').replace('|', ' ')).strip()
        parts = [f"**{name}**"]

        if self.ai_role:
            parts.append(f"Role: {self.ai_role}")
        if self.ai_goal:
            parts.append(f"Goal: {self.ai_goal[:100]}")

        # Tools
        if self.tool_ids:
            tool_names = [t.name for t in self.tool_ids]
            parts.append(f"Tools: {', '.join(tool_names)}")

        # Memories
        if self.memory_ids:
            mem_names = [m.memory_id.name for m in self.memory_ids if m.memory_id]
            if mem_names:
                parts.append(f"Memories: {', '.join(mem_names)}")

        # Model
        if self.model_id:
            parts.append(f"LLM: {self.model_id.name}")

        return "\n".join(parts)


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


class AIAgentMemory(models.Model):
    _name = 'ai.agent.memory'
    _description = 'Agent Memory Link'
    _order = 'sequence asc'

    agent_id = fields.Many2one('ai.agent', required=True, ondelete='cascade',
                                string='Agent')
    memory_id = fields.Many2one('ai.memory', required=True, ondelete='cascade',
                                 string='Memory')
    sequence = fields.Integer(default=10)
