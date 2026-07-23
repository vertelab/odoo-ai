# -*- coding: utf-8 -*-
"""
Session Model (SESS-001, SESS-002) — standalone in ai_agent_core.

If ai_agent is also installed, the model names don't conflict
because our model is different from ai.quest.session.
"""

import json
import logging

from odoo import models, fields, api

_logger = logging.getLogger(__name__)


class AICoreSession(models.Model):
    """Agent session for ai_agent_core."""
    _name = 'ai.core.session'
    _description = 'AI Core Session'
    _order = 'startdate desc'

    session = fields.Char(default=lambda self: self._generate_uuid())
    name = fields.Char(related='session')
    startdate = fields.Datetime(default=lambda self: fields.Datetime.now())
    enddate = fields.Datetime()
    status = fields.Selection([
        ('draft', 'Draft'),
        ('active', 'Active'),
        ('done', 'Done'),
        ('error', 'Error'),
    ], default='draft')

    # Configuration
    config_json = fields.Text('Agent Configuration')
    history_json = fields.Text('Message History')

    # Token tracking
    token_input_total = fields.Integer('Input Tokens', default=0)
    token_output_total = fields.Integer('Output Tokens', default=0)
    cost_estimated = fields.Float('Estimated Cost (USD)', default=0.0)

    # Lifecycle
    round_count = fields.Integer('Round Count', default=0)
    finish_reason = fields.Char('Finish Reason')

    # Relations
    identity_id = fields.Many2one('ai.identity', string='Agent Identity')
    user_id = fields.Many2one('res.users', string='User')

    def _generate_uuid(self):
        import uuid
        return str(uuid.uuid4())

    def save_config(self, config: dict) -> None:
        self.config_json = json.dumps(config, default=str)

    def load_config(self) -> dict:
        return json.loads(self.config_json) if self.config_json else {}

    def save_history(self, messages: list) -> None:
        self.history_json = json.dumps(
            [{"role": getattr(m, 'role', '?'), "content": getattr(m, 'content', '')}
             for m in messages],
            default=str,
        )

    def add_tokens(self, input_tokens: int, output_tokens: int) -> None:
        self.token_input_total += input_tokens
        self.token_output_total += output_tokens

    def mark_done(self, reason: str = "stop") -> None:
        self.status = 'done'
        self.finish_reason = reason
        self.enddate = fields.Datetime.now()

    def mark_error(self, reason: str = "error") -> None:
        self.status = 'error'
        self.finish_reason = reason
        self.enddate = fields.Datetime.now()
