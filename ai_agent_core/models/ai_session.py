# -*- coding: utf-8 -*-
"""ai.quest.session — standalone session model for agent runs."""

import json, logging, uuid
from odoo import models, fields, api

_logger = logging.getLogger(__name__)


class AIQuestSession(models.Model):
    _name = 'ai.quest.session'
    _description = 'AI Quest Session'
    _order = 'create_date desc'

    name = fields.Char(default=lambda self: str(uuid.uuid4())[:8])
    quest_id = fields.Many2one('ai.quest', string='Quest', ondelete='cascade')
    agent_id = fields.Many2one('ai.agent', string='Agent')
    identity_id = fields.Many2one('ai.identity', string='Identity')

    status = fields.Selection([
        ('draft', 'Draft'), ('active', 'Active'),
        ('done', 'Done'), ('error', 'Error'),
    ], default='draft')

    config_json = fields.Text('Configuration')
    history_json = fields.Text('Message History')

    token_input = fields.Integer('Input Tokens', default=0)
    token_output = fields.Integer('Output Tokens', default=0)
    cost_estimated = fields.Float('Cost (USD)', default=0.0)

    create_date = fields.Datetime('Started', default=lambda self: fields.Datetime.now())
    end_date = fields.Datetime('Ended')
    round_count = fields.Integer('Rounds', default=0)
    finish_reason = fields.Char('Finish Reason')

    user_id = fields.Many2one('res.users', default=lambda self: self.env.user)
    company_id = fields.Many2one('res.company', default=lambda self: self.env.company)

    # Thread support
    thread_name = fields.Char('Thread Name')
    session_line_ids = fields.One2many(
        'ai.quest.session.line', 'session_id', string='Messages')
    active = fields.Boolean('Active', default=True)

    def save_config(self, config: dict):
        self.config_json = json.dumps(config, default=str)

    def add_tokens(self, input_t: int, output_t: int):
        self.token_input += input_t
        self.token_output += output_t

    def mark_done(self, reason='stop'):
        self.status = 'done'
        self.finish_reason = reason
        self.end_date = fields.Datetime.now()
