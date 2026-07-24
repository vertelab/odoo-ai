# -*- coding: utf-8 -*-
"""
ai.model — Individual AI model with capabilities and pricing.

Each model belongs to a provider. Capabilities are detected
from the provider API response and model name heuristics.
"""

import logging
from odoo import models, fields, api

_logger = logging.getLogger(__name__)


class AIModel(models.Model):
    _name = 'ai.model'
    _description = 'AI Model'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'provider_id, name asc'

    name = fields.Char('Model ID', required=True,
                        help='The model identifier, e.g. gpt-4o, claude-sonnet-4-20250514')
    display_name = fields.Char('Display Name', compute='_compute_display_name', store=True)
    active = fields.Boolean(default=True)

    # Provider
    provider_id = fields.Many2one('ai.provider', required=True, ondelete='cascade',
                                   string='Provider')
    provider_type = fields.Selection(related='provider_id.provider_type', store=True)

    # Capabilities
    is_vision = fields.Boolean('Vision', help='Supports image input')
    is_embedded = fields.Boolean('Embedding', help='Supports text embeddings')
    is_asr = fields.Boolean('Speech-to-Text', help='Supports audio transcription')
    is_text2image = fields.Boolean('Text-to-Image', help='Supports image generation')
    has_tools = fields.Boolean('Tool Calling', default=True,
                                help='Supports function/tool calling')
    has_json_mode = fields.Boolean('JSON Mode', help='Supports structured JSON output')
    has_streaming = fields.Boolean('Streaming', default=True,
                                    help='Supports token-by-token streaming')

    # Context
    context_window = fields.Integer('Context Window (tokens)', default=128000,
        help='Model context window. Claude=200K, GPT-4o=128K, DeepSeek=1M')
    max_output_tokens = fields.Integer('Max Output Tokens', default=16384)

    # Cost
    cost_input_1k = fields.Float('Input Cost per 1K tokens', digits=(12, 8))
    cost_output_1k = fields.Float('Output Cost per 1K tokens', digits=(12, 8))

    # Status
    status = fields.Selection([
        ('active', 'Active'),
        ('beta', 'Beta'),
        ('deprecated', 'Deprecated'),
    ], default='active')
    status_color = fields.Integer(compute='_compute_status_color')

    # License
    licence = fields.Selection([
        ('apache-2.0', 'Apache 2.0'),
        ('mit', 'MIT'),
        ('commercial', 'Commercial'),
        ('llama-community', 'Llama Community'),
        ('deepseek', 'DeepSeek License'),
        ('gemma', 'Gemma Terms of Use'),
        ('custom', 'Custom'),
    ])

    # Usage
    llm_count = fields.Integer(compute='_compute_llm_count')
    session_line_count = fields.Integer(compute='_compute_session_line_count')

    # Tags
    tag_ids = fields.Many2many('product.tag', string='Tags')

    @api.depends('name', 'provider_id.name')
    def _compute_display_name(self):
        for r in self:
            r.display_name = f"{r.name} ({r.provider_id.name})" if r.provider_id else r.name

    @api.depends('status')
    def _compute_status_color(self):
        for r in self:
            r.status_color = {'active': 10, 'beta': 3, 'deprecated': 1}.get(r.status, 0)

    def _compute_llm_count(self):
        for r in self:
            r.llm_count = self.env['ai.agent.llm'].search_count([('model_id', '=', r.id)])

    def _compute_session_line_count(self):
        for r in self:
            r.session_line_count = self.env['ai.quest.session.line'].search_count([
                ('ai_llm_id', '=', r.id)
            ])
