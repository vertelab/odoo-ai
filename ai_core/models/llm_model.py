# -*- coding: utf-8 -*-
# Copyright (C) 2026 Vertel Sverige AB
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import logging

from odoo import models, fields, api

_logger = logging.getLogger(__name__)


class LLMModel(models.Model):
    """A specific model available from a provider.

    Populated via model discovery (action_discover_models) or manual creation.

    Uses Odoo's built-in ``active`` field — inactive models are hidden
    by default in all searches (standard Odoo behaviour).
    Discovery sets ``active = False`` for models no longer returned by the API.
    """

    _name = "llm.model"
    _description = "LLM Model"
    _order = "provider_type, name"

    name = fields.Char(required=True, index=True)
    provider_type = fields.Char(required=True, index=True)
    active = fields.Boolean(default=True)

    # Capabilities — labels without "Supports" prefix for brevity
    context_window = fields.Integer(help="Max tokens in context window")
    vision = fields.Boolean(string="Vision", default=False)
    tools = fields.Boolean(string="Tools", default=True)
    streaming = fields.Boolean(string="Streaming", default=True)
    embedding = fields.Boolean(string="Embedding", default=False)
    asr = fields.Boolean(string="ASR", default=False)
    embedding_dimensions = fields.Integer(string="Embedding Dims")

    # Metadata
    license = fields.Char()
    created_date = fields.Date()
    extra_metadata = fields.Json()

    # Display
    context_window_display = fields.Char(
        compute="_compute_context_window_display",
    )

    @api.depends("context_window")
    def _compute_context_window_display(self):
        for rec in self:
            if not rec.context_window:
                rec.context_window_display = "Unknown"
            elif rec.context_window >= 1000:
                rec.context_window_display = f"{rec.context_window // 1000}K"
            else:
                rec.context_window_display = str(rec.context_window)

    def name_get(self):
        result = []
        for rec in self:
            name = f"{rec.name} ({rec.provider_type})"
            if not rec.active:
                name += " [inactive]"
            result.append((rec.id, name))
        return result
