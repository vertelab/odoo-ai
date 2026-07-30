# -*- coding: utf-8 -*-
"""ai.agent — standalone with identity, skills, tools, memories, provider, budget."""

import base64
import logging
import requests
from odoo import models, fields, api, _

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

    # Kanban images (related for efficient kanban display)
    partner_image_128 = fields.Binary(related='partner_id.image_128',
                                       string='Partner Image',
                                       help='Avatar from channel partner')

    # ── Buzz workspace membership ──
    partner_id = fields.Many2one('res.partner', string='Channel Partner',
        help='Automatically created when this agent is used in a Buzz workspace. '
             'Enables the agent to be a visible member of a Discuss channel.')
    alias_name = fields.Char('Alias',
        help='Short name for @mentions, e.g. "magnus".')
    trigger_words = fields.Char('Trigger Words',
        help='Comma-separated keywords used for routing in Buzz workspaces.')
    is_buzz_active = fields.Boolean(compute='_compute_is_buzz_active',
        help='True if this agent is assigned to at least one Buzz workspace.')

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
    coworker_count = fields.Integer(compute='_compute_coworker_count')

    def _compute_coworker_count(self):
        for r in self:
            r.coworker_count = self.env['ai.coworker.agent'].search_count([
                ('agent_id', '=', r.id)
            ])

    def _compute_is_buzz_active(self):
        for r in self:
            r.is_buzz_active = bool(self.env['ai.coworker.agent'].search([
                ('agent_id', '=', r.id),
                ('coworker_id.orchestration_mode', '=', 'buzz'),
            ], limit=1))

    def _ensure_partner(self, email_domain='ai.vertel.se'):
        """Create or return res.partner for this agent.

        Called when the agent is added to a Buzz workspace.
        Idempotent — does nothing if partner_id already set.
        """
        self.ensure_one()
        if self.partner_id:
            return self.partner_id
        if not self.name:
            raise ValueError(_('Agent must have a name to create a partner.'))
        alias = (self.alias_name or self.name).lower().replace(' ', '-')
        partner = self.env['res.partner'].sudo().create({
            'name': f'🤖 {self.name}',
            'email': f'agent-{self.id}-{alias}@{email_domain}',
            'is_company': False,
        })
        self.partner_id = partner.id
        return partner

    def _generate_avatar_image(self, avatar_description=''):
        """Generate an avatar image for this agent using an AI image model.

        Falls back to Odoo's default initials avatar if no image model is
        available or the generation fails.
        """
        self.ensure_one()
        if not self.partner_id:
            return False

        prompt = avatar_description or f'Friendly cartoon avatar of {self.name}, professional workplace assistant'
        image_b64 = None

        try:
            model = self.env['ai.model'].sudo().search([
                ('is_text2image', '=', True),
                ('active', '=', True),
            ], limit=1)
            if not model:
                _logger.info('No text2image model available for avatar generation; using fallback')
                return False

            provider = model.provider_id
            if not provider or not provider.base_url:
                return False

            url = provider.base_url.rstrip('/') + '/images/generations'
            headers = {'Content-Type': 'application/json'}
            if provider.api_key:
                headers['Authorization'] = f'Bearer {provider.api_key}'

            payload = {
                'model': model.name,
                'prompt': prompt,
                'n': 1,
                'size': '256x256',
                'response_format': 'b64_json',
            }
            resp = requests.post(url, headers=headers, json=payload, timeout=60)
            resp.raise_for_status()
            data = resp.json()
            b64 = data['data'][0]['b64_json']
            image_b64 = b64 if b64 else data['data'][0].get('url')
        except Exception as e:
            _logger.warning('AI avatar generation failed for agent %s: %s', self.name, e)
            return False

        if image_b64:
            # Ensure pure base64 string (remove data URL prefix if present)
            if isinstance(image_b64, str) and ',' in image_b64:
                image_b64 = image_b64.split(',', 1)[1]
            self.partner_id.sudo().write({'image_1920': image_b64})
            return True
        return False

    def action_regenerate_avatar(self):
        """Regenerate the agent's avatar from its identity or description."""
        self.ensure_one()
        desc = ''
        if self.identity_id:
            desc = f'Friendly cartoon avatar of {self.name}: {self.identity_id.personality or ""}'
        self._generate_avatar_image(desc)
        return {'type': 'ir.actions.act_window_close'}

    def action_get_quests(self):
        quest_ids = self.env['ai.coworker.agent'].search([
            ('agent_id', '=', self.id)
        ]).mapped("coworker_id").ids
        return {
            'name': 'Quests', 'type': 'ir.actions.act_window',
            'res_model': 'ai.coworker', 'view_mode': 'kanban,list,form',
            'target': 'current', 'domain': [('id', 'in', quest_ids)],
        }

    def write(self, vals):
        res = super(AIAgent, self).write(vals)
        if 'name' in vals and self.partner_id:
            self.partner_id.sudo().name = f'🤖 {vals["name"]}'
        if 'alias_name' in vals and self.partner_id:
            alias = (vals['alias_name'] or self.name).lower().replace(' ', '-')
            self.partner_id.sudo().email = f'agent-{self.id}-{alias}@ai.vertel.se'
        return res

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
