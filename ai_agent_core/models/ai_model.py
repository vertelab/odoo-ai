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
    _order = 'provider, name asc'

    name = fields.Char('Model Name', required=True,
                        help='Canonical model identity, e.g. gpt-4o, claude-sonnet-4, deepseek-flash. '
                             'Same for all channels serving the same model.')
    api_name = fields.Char('API Name',
                            help='Model id sent on the wire to the channel (e.g. '
                                 'deepseek/deepseek-flash via Bifrost, deepseek-chat via '
                                 'DeepSeek direct). Empty = fallback to name.')
    display_name = fields.Char('Display Name', compute='_compute_display_name', store=True)
    active = fields.Boolean(default=True)

    # Provider — fältet heter `provider` (renamed från provider_id; data
    # kopieras i migration 18.0.1.112 — oldname stöds inte i detta Odoo-bygge)
    # + `source_provider` (ursprung, t.ex. uppströms bakom en gateway).
    # Logik: source_provider satt = tillverkaren/uppströms bakom en gateway
    # (sätts vid import); tomt = provider är tillverkaren (direktkoppling).
    provider = fields.Many2one('ai.provider', required=True, ondelete='cascade',
                                string='Provider',
                                help='Channel/connection we call (carries base_url, api_key).')
    source_provider = fields.Many2one('ai.provider', string='Source Provider',
                                       help='Maker/upstream behind a gateway. Empty = '
                                            'provider is the maker (direct connection).')

    # Kanban image (related for efficient kanban display)
    provider_image_128 = fields.Binary(related='provider.image_128',
                                        string='Provider Image',
                                        help='Avatar from provider')
    provider_type = fields.Selection(related='provider.provider_type', store=True)
    real_provider = fields.Char('Real Provider', compute='_compute_real_provider', store=True,
                                 help='The maker (source_provider or provider).')

    def _get_api_name(self):
        """Wire id: api_name, fallback till name (bakåtkompatibelt)."""
        self.ensure_one()
        return self.api_name or self.name

    def _get_maker(self):
        """Tillverkaren: source_provider om satt, annars provider (direktkoppling)."""
        self.ensure_one()
        return self.source_provider or self.provider

    @api.depends('source_provider.name', 'provider.name')
    def _compute_real_provider(self):
        """Real provider = tillverkaren (source_provider eller provider)."""
        for r in self:
            maker = r.source_provider or r.provider
            r.real_provider = maker.name if maker else ''

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

    # Systemtoken pricing (admin-styrd)
    sys_multiplier = fields.Float('Systemtoken-multiplikator', default=1.0,
        help='How many systemtokens 1 real token costs. 1.0 = DeepSeek, 5.0 = GPT-4o, 6.0 = Claude. Includes Vertel margin.')
    provider_cost_1M = fields.Float('Provider $/1M tokens',
        help='What the provider actually charges per 1M tokens. For admin insight only.')

    _sql_constraints = [
        ('name_provider_uniq', 'UNIQUE(name, provider)',
         'A model (name) can only exist once per provider (channel).'),
    ]

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
    tag_ids = fields.Many2many('ai.tag', string='Tags')

    @api.depends('name', 'provider.name')
    def _compute_display_name(self):
        for r in self:
            r.display_name = f"[{r.provider.name}] {r.name}" if r.provider else r.name

    @api.depends('status')
    def _compute_status_color(self):
        for r in self:
            r.status_color = {'active': 10, 'beta': 3, 'deprecated': 1}.get(r.status, 0)

    def _compute_llm_count(self):
        for r in self:
            r.llm_count = self.env['ai.agent.llm'].search_count([('model_id', '=', r.id)])

    def _compute_session_line_count(self):
        for r in self:
            r.session_line_count = self.env['ai.coworker.session.line'].search_count([
                ('ai_llm_id', '=', r.id)
            ])
