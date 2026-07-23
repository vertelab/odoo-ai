# -*- coding: utf-8 -*-
"""
ai.provider — LLM Provider definition.

Manages provider connection details and API keys.
Wizard: fetch provider info from name/URL.
Smart button: fetch available models with capabilities.
"""

import json, logging, re, urllib.request, ssl
from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)

PROVIDER_TYPES = [
    ('openai', 'OpenAI'),
    ('anthropic', 'Anthropic'),
    ('deepseek', 'DeepSeek'),
    ('google', 'Google'),
    ('cerebras', 'Cerebras'),
    ('groq', 'Groq'),
    ('ollama', 'Ollama'),
    ('openrouter', 'OpenRouter'),
    ('bifrost', 'Bifrost Gateway'),
    ('custom', 'Custom (OpenAI-compatible)'),
]


class AIProvider(models.Model):
    _name = 'ai.provider'
    _description = 'AI Provider'
    _inherit = ['mail.thread']
    _order = 'name asc'

    name = fields.Char(required=True)
    active = fields.Boolean(default=True)
    provider_type = fields.Selection(PROVIDER_TYPES, required=True, default='custom')
    base_url = fields.Char('API Base URL', help='e.g. https://api.openai.com/v1')
    api_key = fields.Char('API Key')
    is_key_required = fields.Boolean(default=True)

    # Status
    status = fields.Selection([
        ('draft', 'Draft'),
        ('confirmed', 'Confirmed'),
        ('error', 'Error'),
    ], default='draft')

    # Stats
    model_count = fields.Integer(compute='_compute_model_count')
    model_ids = fields.One2many('ai.model', 'provider_id', string='Models')
    last_checked = fields.Datetime('Last Model Sync')

    # Config
    timeout = fields.Integer('Timeout (s)', default=120)
    retry_count = fields.Integer('Max Retries', default=3)

    @api.depends('model_ids')
    def _compute_model_count(self):
        for r in self:
            r.model_count = len(r.model_ids)

    # -- Actions --
    def action_fetch_models(self):
        """Smart button: fetch available models from this provider."""
        self.ensure_one()
        if not self.base_url:
            raise UserError(_('Set a base URL first'))
        if self.is_key_required and not self.api_key:
            raise UserError(_('API key required for this provider'))

        # Fetch from provider API
        count = self._fetch_models_from_api()
        self.last_checked = fields.Datetime.now()
        self.status = 'confirmed'

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Models Synced'),
                'message': _('%d models imported from %s') % (count, self.name),
                'type': 'success',
            }
        }

    def _fetch_models_from_api(self):
        """Fetch models from provider's /v1/models endpoint."""
        self.ensure_one()
        url = self.base_url.rstrip('/') + '/models'
        headers = {'Authorization': f'Bearer {self.api_key}'}
        if self.provider_type == 'anthropic':
            headers = {'x-api-key': self.api_key, 'anthropic-version': '2023-06-01'}
            url = self.base_url.rstrip('/') + '/models'

        try:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=30, context=ctx) as resp:
                data = json.loads(resp.read().decode())
        except Exception as e:
            _logger.error("Failed to fetch models from %s: %s", self.name, e)
            raise UserError(_('Failed to fetch models: %s') % str(e))

        models = data.get('data', data if isinstance(data, list) else [])
        count = 0
        for m in models:
            model_id = m.get('id', '')
            if not model_id or model_id.startswith('ft:'):
                continue  # skip fine-tuned models
            self._import_model(m)
            count += 1
        return count

    def _import_model(self, data: dict):
        """Create or update ai.model from provider data."""
        model_id = data.get('id', '')
        existing = self.env['ai.model'].search([
            ('name', '=', model_id),
            ('provider_id', '=', self.id),
        ], limit=1)

        vals = {
            'name': model_id,
            'provider_id': self.id,
            'status': 'active',
        }
        if data.get('context_window'):
            vals['context_window'] = data['context_window']
        if data.get('max_output_tokens'):
            vals['max_output_tokens'] = data['max_output_tokens']

        # Detect capabilities from model name
        if any(k in model_id.lower() for k in ('vision', 'gpt-4o', 'gemini', 'claude-3', 'claude-4')):
            vals['is_vision'] = True
        if any(k in model_id.lower() for k in ('embed', 'text-embedding')):
            vals['is_embedded'] = True
        if any(k in model_id.lower() for k in ('whisper', 'tts', 'audio')):
            vals['is_asr'] = True
        if any(k in model_id.lower() for k in ('dall-e', 'imagen')):
            vals['is_text2image'] = True

        if existing:
            existing.write(vals)
        else:
            self.env['ai.model'].create(vals)

    def action_test_connection(self):
        """Test provider connection."""
        self.ensure_one()
        try:
            self._fetch_models_from_api()
            self.status = 'confirmed'
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Connection OK'),
                    'message': _('%s is reachable') % self.name,
                    'type': 'success',
                }
            }
        except Exception as e:
            self.status = 'error'
            raise UserError(_('Connection failed: %s') % str(e))


class AIProviderWizard(models.TransientModel):
    _name = 'ai.provider.wizard'
    _description = 'Discover Provider'

    name = fields.Char('Provider Name')
    url = fields.Char('URL or Domain')

    def action_discover(self):
        """Auto-detect provider type and base URL from name/URL."""
        self.ensure_one()
        vals = {
            'name': self.name,
            'status': 'draft',
        }

        url_lower = (self.url or '').lower()
        name_lower = (self.name or '').lower()

        # Auto-detect provider type
        if 'openai' in url_lower or 'openai' in name_lower:
            vals['provider_type'] = 'openai'
            vals['base_url'] = 'https://api.openai.com/v1'
        elif 'anthropic' in url_lower or 'claude' in name_lower:
            vals['provider_type'] = 'anthropic'
            vals['base_url'] = 'https://api.anthropic.com/v1'
        elif 'deepseek' in url_lower or 'deepseek' in name_lower:
            vals['provider_type'] = 'deepseek'
            vals['base_url'] = 'https://api.deepseek.com/v1'
        elif 'google' in url_lower or 'gemini' in name_lower:
            vals['provider_type'] = 'google'
            vals['base_url'] = 'https://generativelanguage.googleapis.com/v1beta'
        elif 'cerebras' in url_lower or 'cerebras' in name_lower:
            vals['provider_type'] = 'cerebras'
            vals['base_url'] = 'https://api.cerebras.ai/v1'
        elif 'groq' in url_lower or 'groq' in name_lower:
            vals['provider_type'] = 'groq'
            vals['base_url'] = 'https://api.groq.com/openai/v1'
        elif 'ollama' in url_lower or 'ollama' in name_lower:
            vals['provider_type'] = 'ollama'
            vals['base_url'] = 'http://localhost:11434/v1'
            vals['is_key_required'] = False
        elif 'bifrost' in name_lower or '192.168.11.150' in url_lower:
            vals['provider_type'] = 'bifrost'
            vals['base_url'] = 'http://192.168.11.150:8080/v1'
            vals['is_key_required'] = False
        elif 'berget' in url_lower or 'berget' in name_lower:
            vals['provider_type'] = 'custom'
            vals['base_url'] = 'https://berget.ai/v1'
        elif self.url:
            vals['provider_type'] = 'custom'
            vals['base_url'] = self.url.rstrip('/') + '/v1' if '/v1' not in self.url else self.url

        provider = self.env['ai.provider'].create(vals)

        return {
            'type': 'ir.actions.act_window',
            'res_model': 'ai.provider',
            'res_id': provider.id,
            'view_mode': 'form',
            'target': 'current',
        }
