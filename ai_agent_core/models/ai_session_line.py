# -*- coding: utf-8 -*-
"""
ai.coworker.session.line — Individual messages within a thread.

Each line represents one message in a conversation thread.
"""
from odoo import models, fields, api
from datetime import timedelta


class AICoworkerSessionLine(models.Model):
    _name = 'ai.coworker.session.line'
    _description = 'Session Message Line'
    _order = 'session_id, sequence asc'

    session_id = fields.Many2one(
        'ai.coworker.session', required=True, ondelete='cascade',
        string='Thread',
    )
    # Vilken agent/roll/skill/tool som producerade denna rad — för spårning
    # per agent & modell samt smartknappar från ai.agent/ai.tool/ai.skill.
    agent_id = fields.Many2one('ai.agent', string='Agent', index=True,
        help='Den agent som producerade denna meddelanderad. Fylls vid '
             'multi-agent-delegering (Väg A) och enkel-assistentrader där '
             'agenten är känd.')
    skill_id = fields.Many2one('ai.skill', string='Skill', index=True,
        help='Den skill som byggdes/testades i denna meddelanderad (där det '
             'är känt, t.ex. från agentens skill eller en skill-sessionskontext).')
    tool_id = fields.Many2one('ai.tool', string='Tool', index=True,
        help='Verktyget som anropades (för tool-rader). Fylls från '
             'tool_name mot ai.tool så att ai.tool kan lista sina meddelanden.')
    sequence = fields.Integer('Order', default=10)
    role = fields.Selection([
        ('user', 'User'),
        ('assistant', 'Assistant'),
        ('tool', 'Tool'),
        ('system', 'System'),
    ], required=True, default='user')
    content = fields.Text('Message Content')
    debug_info = fields.Text('Debug/Resonemang',
        help='Agentens resonemang/narrering (visas inte i svaret till '
             'användaren, men sparas här för granskning).')
    source_urls = fields.Text('Käll-URL:er',
        help='URL:er som agenten använde (en per rad).')
    tool_calls = fields.Text('Tool Calls (JSON)')
    tool_name = fields.Char('Tool Name')
    token_input = fields.Integer('Input Tokens', default=0)
    token_output = fields.Integer('Output Tokens', default=0)

    # Systemtoken tracking
    model_real = fields.Char('Model Used',
        help='The actual model name returned by the provider (e.g. claude-sonnet-4-20250514)')
    sys_multiplier = fields.Float('Systemtoken-multiplikator', default=1.0,
        help='Multiplier from ai.model at the time this line was created')
    token_sys = fields.Integer('Systemtokens', compute='_compute_token_sys', store=True,
        help='(token_input + token_output) × sys_multiplier')

    @api.depends('token_input', 'token_output', 'sys_multiplier')
    def _compute_token_sys(self):
        for line in self:
            line.token_sys = int((line.token_input + line.token_output) * line.sys_multiplier)

    @api.model
    def _tokens_since(self, days=30, extra=(), create_field='create_date'):
        """Summa token_input+token_output för rader yngre än `days`, som valfritt
        matchar `extra`-domänvillkor. Används av smartknappar på ai.model/
        ai.agent/ai.tool/ai.skill för att visa senaste månadens tokenförbrukning.
        """
        since = fields.Datetime.now() - timedelta(days=max(days, 1))
        domain = [(create_field, '>=', since)] + list(extra)
        rows = self.search(domain)
        return sum((r.token_input or 0) + (r.token_output or 0) for r in rows)
