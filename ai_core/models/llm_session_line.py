# -*- coding: utf-8 -*-
# Copyright (C) 2026 Vertel Sverige AB
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

"""Token tracking per LLM call."""

import logging

from odoo import models, fields, api

_logger = logging.getLogger(__name__)


class LLMSessionLine(models.Model):
    """Records a single LLM invocation — provider, model, tokens, cost."""

    _name = "llm.session.line"
    _description = "LLM Session Line"
    _order = "create_date desc"

    provider_type = fields.Char(index=True)
    model_name = fields.Char(index=True)

    # Token counts
    input_tokens = fields.Integer(default=0)
    output_tokens = fields.Integer(default=0)
    cached_tokens = fields.Integer(default=0)
    total_tokens = fields.Integer(
        compute="_compute_total_tokens", store=True
    )

    # Cost (if provider reports it)
    cost_usd = fields.Float(digits=(10, 6))
    cost_currency = fields.Char(default="USD")

    # Metadata
    duration_ms = fields.Integer(help="API call duration in milliseconds")
    is_error = fields.Boolean(default=False)
    error_message = fields.Text()

    # References (populated by ai_agent later)
    session_id = fields.Many2one("ai.quest.session", ondelete="set null")
    agent_id = fields.Many2one("ai.agent", ondelete="set null")
    quest_id = fields.Many2one("ai.quest", ondelete="set null")

    @api.depends("input_tokens", "output_tokens")
    def _compute_total_tokens(self):
        for rec in self:
            rec.total_tokens = rec.input_tokens + rec.output_tokens
