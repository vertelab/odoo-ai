# -*- coding: utf-8 -*-
"""
Extended Session Model (SESS-001, SESS-002).

SESS-001: ai.quest.session extended with config_json, history_json, token tracking.
SESS-002: Session lifecycle — draft → active → done (or error).
"""

import json
import logging

from odoo import models, fields, api

_logger = logging.getLogger(__name__)


class AIQuestSession(models.Model):
    """Extended quest session for ai_agent_core."""
    _inherit = 'ai.quest.session'

    # ── Configuration ──
    config_json = fields.Text(
        string='Agent Configuration',
        help='Serialized AgentConfig for this session (model, temperature, max_tokens, etc.)',
    )
    history_json = fields.Text(
        string='Message History',
        help='Serialized conversation history for the agent loop.',
    )

    # ── Token tracking (extends existing session_line_ids) ──
    token_input_total = fields.Integer(
        string='Total Input Tokens',
        default=0,
        help='Accumulated input tokens across all rounds in this session.',
    )
    token_output_total = fields.Integer(
        string='Total Output Tokens',
        default=0,
        help='Accumulated output tokens across all rounds in this session.',
    )
    cost_estimated = fields.Float(
        string='Estimated Cost (USD)',
        default=0.0,
        help='Estimated cost based on model pricing and token usage.',
    )

    # ── Session lifecycle (SESS-002) ──
    round_count = fields.Integer(
        string='Round Count',
        default=0,
        help='Number of agent loop rounds executed in this session.',
    )
    finish_reason = fields.Char(
        string='Finish Reason',
        help='Why the session ended: stop, max_rounds, timeout, error, cancelled.',
    )

    def save_config(self, config: dict) -> None:
        """Save AgentConfig for this session."""
        self.config_json = json.dumps(config, default=str)

    def load_config(self) -> dict:
        """Load AgentConfig from this session."""
        if self.config_json:
            return json.loads(self.config_json)
        return {}

    def save_history(self, messages: list) -> None:
        """Save conversation history."""
        self.history_json = json.dumps(
            [{"role": m.role.value, "content": m.content} for m in messages],
            default=str,
        )

    def load_history(self) -> list:
        """Load conversation history."""
        if self.history_json:
            return json.loads(self.history_json)
        return []

    def add_tokens(self, input_tokens: int, output_tokens: int) -> None:
        """Accumulate token usage."""
        self.token_input_total += input_tokens
        self.token_output_total += output_tokens

    def mark_done(self, reason: str = "stop") -> None:
        """Mark session as completed."""
        self.status = 'done'
        self.finish_reason = reason
        self.enddate = fields.Datetime.now()

    def mark_error(self, reason: str = "error") -> None:
        """Mark session as failed."""
        self.status = 'error'
        self.finish_reason = reason
        self.enddate = fields.Datetime.now()
