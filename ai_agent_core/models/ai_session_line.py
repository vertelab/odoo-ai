# -*- coding: utf-8 -*-
"""
ai.quest.session.line — Individual messages within a thread.

Each line represents one message in a conversation thread.
"""
from odoo import models, fields


class AIQuestSessionLine(models.Model):
    _name = 'ai.quest.session.line'
    _description = 'Session Message Line'
    _order = 'session_id, sequence asc'

    session_id = fields.Many2one(
        'ai.quest.session', required=True, ondelete='cascade',
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
    tool_calls = fields.Text('Tool Calls (JSON)')
    tool_name = fields.Char('Tool Name')
    token_input = fields.Integer('Input Tokens', default=0)
    token_output = fields.Integer('Output Tokens', default=0)
