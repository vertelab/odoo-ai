# -*- coding: utf-8 -*-
"""
ai.coworker.session.line — Individual messages within a thread.

Each line represents one message in a conversation thread.
"""
from odoo import models, fields, api


class AICoworkerSessionLine(models.Model):
    _name = 'ai.coworker.session.line'
    _description = 'Session Message Line'
    _order = 'session_id, sequence asc'

    session_id = fields.Many2one(
        'ai.coworker.session', required=True, ondelete='cascade',
        string='Thread',
    )
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
