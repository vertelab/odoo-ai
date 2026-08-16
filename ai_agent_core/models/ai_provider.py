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
    @api.constrains('api_key')
    def _check_api_key_sane(self):
        """Förhindra att flerradig text klistras in som API-nyckel (skulle
        krascha HTTP-headers med 'Illegal header value')."""
        for rec in self:
            if rec.api_key and any(c in rec.api_key for c in '\r\n'):
                raise ValidationError(
                    'API-nyckeln får inte innehålla radbrytningar — '
                    'klistra bara in själva nyckeln.'
                )


    _description = 'AI Provider'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'name asc'

    name = fields.Char(required=True)
    active = fields.Boolean(default=True)
    provider_type = fields.Selection(PROVIDER_TYPES, required=True, default='custom')
    base_url = fields.Char('API Base URL', help='e.g. https://api.openai.com/v1')
    api_key = fields.Char('API Key')
    is_key_required = fields.Boolean(default=True)

    # Datadrivna skillnader (fix-provider-resolution): allt som skiljer en
    # provider från en annan lagras på recordet — aldrig hårdkodat i koden.
    # Ingen pillar/env-koppling: api_key ligger på recordet, fylls i via UI.
    is_bifrost = fields.Boolean(
        'Bifrost-gateway',
        help='Använd X-Virtual-Key-headern (Bifrost LLM Gateway).',
        default=False,
    )
    api_style = fields.Selection([
        ('openai', 'OpenAI-kompatibel (/chat/completions)'),
        ('anthropic', 'Anthropic (/v1/messages)'),
    ], default='openai', string='API-stil')

    # Status
    status = fields.Selection([
        ('draft', 'Draft'),
        ('confirmed', 'Confirmed'),
        ('error', 'Error'),
    ], default='draft')

    # Config-styrd (bifrost-client-provisioning): providern provisioneras via
    # odoo.conf (ai_provider_endpoint/api_key/list) och synkas automatiskt av
    # cron — generiskt, ingen Bifrost-hårdkodning i modulen.
    auto_sync = fields.Boolean(
        'Config-styrd (auto-sync)',
        help='Provisionerad via odoo.conf — cron synkar modeller automatiskt.',
        default=False,
    )

    # Stats
    model_count = fields.Integer(compute='_compute_model_count')
    model_ids = fields.One2many('ai.model', 'provider', string='Models')
    last_checked = fields.Datetime('Last Model Sync')

    # Image
    image_128 = fields.Binary(string='Image', attachment=True)

    # Config
    timeout = fields.Integer('Timeout (s)', default=120)
    retry_count = fields.Integer('Max Retries', default=3)

    @api.depends('model_ids')
    def _compute_model_count(self):
        for r in self:
            r.model_count = len(r.model_ids)

    # -- Datadrivna flaggor (fix-provider-resolution) --
    @api.onchange('provider_type')
    def _onchange_provider_type(self):
        """Sätt datadrivna flaggor (is_bifrost/api_style) när
        provider_type ändras. Överskrivs av action_discover vid auto-detection.
        """
        if not self.provider_type:
            return
        flags = self._flags_from_type(self.provider_type)
        self.is_bifrost = flags['is_bifrost']
        self.api_style = flags['api_style']

    @staticmethod
    def _flags_from_type(provider_type: str) -> dict:
        """Default-flaggorna för en provider_type (data-nära, i modellen).

        Bifrost → X-Virtual-Key-header; anthropic → /v1/messages;
        övriga → bearer/openai.
        """
        type_lower = (provider_type or '').lower()
        if type_lower == 'bifrost':
            return {'is_bifrost': True, 'api_style': 'openai'}
        if type_lower == 'anthropic':
            return {'is_bifrost': False, 'api_style': 'anthropic'}
        return {'is_bifrost': False, 'api_style': 'openai'}

    # -- Config-styrd provisionering (bifrost-client-provisioning) --
    # Läser odoo.conf (ai_provider_*) — mönster user_scim (odoo_config.get +
    # ir.config_parameter-fallback). Generiska nyckelnamn: modulen vet inget
    # om Bifrost; Salt mappar pillar → dessa nycklar.

    @staticmethod
    def _config_get(key: str, default: str = ''):
        try:
            from odoo.tools import config as odoo_config
            return odoo_config.get(key, '') or default
        except Exception:
            return default

    @api.model
    def _reconcile_from_config(self):
        """Find-or-create provider från odoo.conf-params (idempotent).

        Generisk OpenAI-kompatibel provider (Bearer) — auto_sync=True,
        status=confirmed. Returnerar (provider_or_False, changed: bool).
        """
        endpoint = self._config_get('ai_provider_endpoint')
        if not endpoint:
            return self.browse(), False
        endpoint = endpoint.rstrip('/')
        api_key = self._config_get('ai_provider_api_key')
        provider = self.search([('base_url', '=', endpoint)], limit=1)
        if not provider:
            provider = self.create({
                'name': self._config_get('ai_provider_name', 'AI Provider (config)'),
                'provider_type': 'custom',
                'base_url': endpoint,
                'api_key': api_key,
                'api_style': 'openai',
                'is_key_required': bool(api_key),
                'auto_sync': True,
                'status': 'confirmed',
            })
            _logger.info('Provider skapad från config: %s (%s)', provider.name, endpoint)
            return provider, True
        vals = {}
        if provider.api_key != api_key:
            vals['api_key'] = api_key
        if not provider.auto_sync:
            vals['auto_sync'] = True
        if provider.status != 'confirmed':
            vals['status'] = 'confirmed'
        if vals:
            provider.write(vals)
        return provider, bool(vals)

    @api.model
    def _apply_default_model_from_config(self):
        """Sätt ai_agent_core.default_model_id från ai_default_model (name/api_name)."""
        default_model = self._config_get('ai_default_model')
        if not default_model:
            return False
        model = self.env['ai.model'].sudo().search([
            '|', ('name', '=', default_model), ('api_name', '=', default_model),
        ], limit=1)
        if not model:
            return False
        param = self.env['ir.config_parameter'].sudo().get_param(
            'ai_agent_core.default_model_id')
        if str(model.id) != param:
            self.env['ir.config_parameter'].sudo().set_param(
                'ai_agent_core.default_model_id', model.id)
            return True
        return False

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

    @api.model
    def _bifrost_base_url(self):
        """Bifrost-gatewayens bas-URL från konfiguration (8081 = combo-adapter)."""
        url = (
            self.env['ir.config_parameter'].get_param('bifrost.combo_adapter_url')
            or self.env['ir.config_parameter'].get_param('bifrost.api_url')
            or 'http://192.168.11.150:8081'
        )
        return url.rstrip('/') + '/v1'

    def _fetch_models_from_api(self):
        """Fetch models from provider's /v1/models endpoint.

        Bifrost returns models in format "provider/model_name"
        (e.g., "openrouter/anthropic/claude-sonnet-4").
        These are upstream provider names — the provider IS Bifrost,
        the prefix is the upstream routing hint.
        Importen kanonicaliserar: name = identitet (prefix-strippat),
        api_name = full path (wire), source_provider = tillverkaren.
        """
        self.ensure_one()
        url = self.base_url.rstrip('/') + '/models'
        headers = {}
        if self.provider_type == 'bifrost':
            # Bifrost-gatewayen kräver admin-nyckel för att lista modeller
            # (X-Virtual-Key ger tom lista). Fallback till api_key om satt.
            admin_key = self.env['ir.config_parameter'].get_param(
                'bifrost.admin_api_key', '')
            if admin_key:
                headers['Authorization'] = f'Bearer {admin_key}'
            elif self.api_key:
                headers['Authorization'] = f'Bearer {self.api_key}'
            else:
                # Sista fallback: gatewayens vk-vertel-värde (= admin-nyckeln)
                headers['X-Virtual-Key'] = (
                    admin_key or 'sk-bf-7885f84e-e3a0-470a-accc-0a8295f128bd'
                )
        elif self.api_key:
            headers['Authorization'] = f'Bearer {self.api_key}'

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
                continue
            self._import_model(model_id)
            count += 1
        return count

    # -- Maker-upplösning (provider-model-offering) --

    def _is_gateway(self) -> bool:
        """Är denna provider en gateway/återförsäljare (inte tillverkare)?"""
        return self.provider_type in ('bifrost', 'openrouter') or bool(self.is_bifrost)

    def _resolve_maker(self, model_id: str):
        """Resolve maker (tillverkare) + kanoniskt namn från ett model-id.

        Längsta prefix-match mot kända DIREKTA provider-record (gateways
        exkluderas — annars matchar 'openrouter' i
        'openrouter/anthropic/claude-sonnet-4' felaktigt). Fallback för
        gateways: näst sista segmentet (find-or-create label-record).

        Returns:
            (maker_or_False, canonical_name)
        """
        if '/' not in (model_id or ''):
            return False, model_id
        parts = model_id.split('/')
        gateway_types = ('bifrost', 'openrouter')
        known = self.env['ai.provider'].search([
            ('provider_type', 'not in', list(gateway_types)),
            ('is_bifrost', '=', False),
        ])
        # Längst prefix först
        for i in range(len(parts) - 1, 0, -1):
            prefix = '/'.join(parts[:i]).lower()
            for p in known:
                if p.name and p.name.lower() == prefix:
                    return p, '/'.join(parts[i:])
        # Fallback (bara för gateways): sista segmentet före modellnamnet
        if self._is_gateway() and len(parts) >= 2:
            maker_name = parts[-2]
            maker = self.env['ai.provider'].search(
                [('name', 'ilike', maker_name)], limit=1)
            if not maker:
                maker = self.env['ai.provider'].create({
                    'name': maker_name.capitalize(),
                    'provider_type': 'custom',
                    'is_key_required': False,
                    'status': 'draft',
                })
            return maker, parts[-1]
        return False, model_id

    def _import_model(self, model_id: str):
        """Create or update ai.model from provider data (kanonicaliserat).

        - name = kanoniskt identitetsnamn (prefix-strippat)
        - api_name = full path (wire-id)
        - source_provider = tillverkare (gateway) / tom (direkt)
        - find-or-update på (name, provider) — UNIQUE-safe, idempotent
        For NEW models: sets default sys_multiplier based on model name heuristics.
        For EXISTING models: preserves manually set sys_multiplier.
        """
        maker, canonical = self._resolve_maker(model_id)
        name = canonical or model_id
        source_provider = maker.id if maker and maker.id != self.id else False

        existing = self.env['ai.model'].search([
            ('name', '=', name),
            ('provider', '=', self.id),
        ], limit=1)

        vals = {
            'name': name,
            'api_name': model_id,
            'provider': self.id,
            'source_provider': source_provider,
            'status': 'active',
        }

        # Detect capabilities from model name
        name_lower = model_id.lower()
        if any(k in name_lower for k in ('vision', 'gpt-4o', 'gemini', 'claude-3', 'claude-4')):
            vals['is_vision'] = True
        if any(k in name_lower for k in ('embed', 'text-embedding')):
            vals['is_embedded'] = True
        if any(k in name_lower for k in ('whisper', 'tts', 'audio')):
            vals['is_asr'] = True
        if any(k in name_lower for k in ('dall-e', 'imagen')):
            vals['is_text2image'] = True

        if existing:
            # Update existing — preserve sys_multiplier if manually set
            existing.write(vals)
        else:
            # New model — set default sys_multiplier
            vals['sys_multiplier'] = self._default_sys_multiplier(model_id)
            self.env['ai.model'].create(vals)

    def _default_sys_multiplier(self, model_id: str) -> float:
        """Determine default sys_multiplier for a model based on its name.
        
        These defaults include Vertel's margin and reflect the relative
        cost/quality of each model family.
        """
        name = model_id.lower()
        
        # Embedding models — very cheap
        if any(k in name for k in ('embed', 'text-embedding')):
            return 0.1
        
        # Cheap/fast models
        if any(k in name for k in ('deepseek', 'gpt-oss', 'llama-3.1-8b', 'gemma',
                                    'allam', 'orpheus', 'qwen-2.5', 'ministral')):
            return 1.0
        
        # Budget balanced
        if any(k in name for k in ('gpt-4o-mini', 'llama-3.3-70b', 'mistral',
                                    'claude-haiku', 'haiku', 'mixtral')):
            return 1.5
        
        # Mid-tier
        if any(k in name for k in ('gpt-4o', 'gpt-4-', 'command-r', 'llama-4')):
            return 5.0
        
        # Premium models
        if any(k in name for k in ('claude-sonnet', 'claude-3', 'claude-4',
                                    'claude-opus', 'gemini-2', 'gpt-5')):
            return 6.0
        
        # Audio/speech models
        if any(k in name for k in ('whisper', 'tts', 'audio')):
            return 2.0
        
        # Default for unknown models
        return 1.0

    def action_set_default_multipliers(self):
        """Admin action: recalculate sys_multiplier defaults for all models.
        
        Only sets multiplier on models that still have the default 1.0
        (does NOT overwrite manually adjusted multipliers).
        """
        self.ensure_one()
        models = self.env['ai.model'].search([
            ('provider', '=', self.id),
            ('sys_multiplier', '=', 1.0),  # only untouched defaults
        ])
        count = 0
        for m in models:
            new_mult = self._default_sys_multiplier(m.name)
            if new_mult != 1.0:
                m.sys_multiplier = new_mult
                count += 1
        return count

    def action_view_models(self):
        """Smart button: open models linked to this provider."""
        self.ensure_one()
        return {
            'name': _('Models — %s') % self.name,
            'type': 'ir.actions.act_window',
            'res_model': 'ai.model',
            'view_mode': 'list,kanban,form',
            'target': 'current',
            'domain': [('provider', '=', self.id)],
            'context': {'default_provider': self.id},
        }

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

    def _text_to_speech(self, text, model=None, voice=None):
        """Text → ljud (TTS) via providern (OpenAI-kompatibel /v1/audio/speech).

        Anropas av pbx_ai för samtals-coworker (receptionist): texten som
        AI:n vill säga → ljud-bytes → ARI playback.

        Args:
            text: texten att tala
            model: tts-modell (default: första is_asr-modellen, fallback
                   'tts-1')
            voice: röst (default 'alloy')

        Returns:
            bytes: ljudinnehåll (mp3) eller b'' vid fel
        """
        self.ensure_one()
        import requests
        try:
            base = (self.base_url or '').rstrip('/')
            url = base + '/audio/speech'
            if not base:
                return b''
            if not model:
                # Hitta första is_asr-modellen, annars tts-1
                asr_model = self.env['ai.model'].search([
                    ('provider', '=', self.id),
                    ('is_asr', '=', True),
                ], limit=1)
                model = (asr_model.api_name or asr_model.name
                         if asr_model else 'tts-1')
            headers = {'Content-Type': 'application/json'}
            if self.api_key:
                headers['Authorization'] = 'Bearer %s' % self.api_key
            if self.is_bifrost:
                headers['X-Virtual-Key'] = self.api_key or ''
            resp = requests.post(
                url,
                json={
                    'model': model,
                    'input': text,
                    'voice': voice or 'alloy',
                    'response_format': 'mp3',
                },
                headers=headers,
                timeout=60,
            )
            if resp.status_code == 200:
                return resp.content
            _logger.warning('TTS failed %s: %s', resp.status_code,
                            resp.text[:200])
            return b''
        except Exception as e:
            _logger.warning('TTS error: %s', e)
            return b''


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
        if 'bifrost' in name_lower or '192.168.11.150' in url_lower:
            vals['provider_type'] = 'bifrost'
            vals['base_url'] = self._bifrost_base_url()
            vals['is_key_required'] = False
        elif 'berget' in url_lower or 'berget' in name_lower:
            vals['provider_type'] = 'custom'
            vals['base_url'] = 'https://berget.ai/v1'
        elif 'openrouter' in url_lower or 'openrouter' in name_lower:
            vals['provider_type'] = 'openrouter'
            vals['base_url'] = 'https://openrouter.ai/api/v1'
        elif 'openai' in url_lower or 'openai' in name_lower:
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
        elif self.url:
            vals['provider_type'] = 'custom'
            vals['base_url'] = self.url.rstrip('/') + '/v1' if '/v1' not in self.url else self.url

        # Datadrivna flaggor (is_bifrost/api_style) från typen
        flags = self.env['ai.provider']._flags_from_type(
            vals.get('provider_type', 'custom'))
        vals['is_bifrost'] = flags['is_bifrost']
        vals['api_style'] = flags['api_style']

        provider = self.env['ai.provider'].create(vals)

        return {
            'type': 'ir.actions.act_window',
            'res_model': 'ai.provider',
            'res_id': provider.id,
            'view_mode': 'form',
            'target': 'current',
        }
