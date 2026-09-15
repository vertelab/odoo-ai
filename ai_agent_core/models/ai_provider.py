# -*- coding: utf-8 -*-
"""
ai.provider — LLM Provider definition.

Manages provider connection details and API keys.
Wizard: fetch provider info from name/URL.
Smart button: fetch available models with capabilities.
"""

import json, logging, re, time, urllib.request, ssl
from typing import Optional

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
            # /v1/models på combo-adaptern är entitlement-baserad — returnerar
            # de combos/modeller som NYCKELN (provider.api_key) har rätt till.
            # Admin-nyckeln (bifrost.admin_api_key) är bara för /admin/* och
            # ger tom lista här (0 modeller). Använd providerns egen VK-nyckel
            # först; fallback till admin-nyckeln bara om api_key saknas.
            if self.api_key:
                headers['Authorization'] = f'Bearer {self.api_key}'
            else:
                admin_key = self.env['ir.config_parameter'].get_param(
                    'bifrost.admin_api_key', '')
                headers['X-Virtual-Key'] = admin_key or ''
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
        # Berika varje modell med context/pris från providerns rika
        # /v1/config-katalog (där sådan finns — t.ex. combo-adaptern).
        # /v1/models ger bara modell-id:n; /v1/config ger contextWindow,
        # maxTokens och cost.pris. Utan detta behåller ai.model-posterna
        # fältdafaulten (128k / ingen kostnad).
        config_map = self._fetch_config_map()
        count = 0
        for m in models:
            model_id = m.get('id', '')
            if not model_id or model_id.startswith('ft:'):
                continue
            self._import_model(model_id, config_map.get(model_id))
            count += 1
        return count

    def _fetch_config_map(self):
        """Hämta providerns rika /v1/config-katalog som {model_id: dict}.

        Returnerar en map med context/maxOutput/cost per modell-id. Tyst
        fallback till {} om endpointen saknas / misslyckas (ej kritiskt).
        """
        headers = {}
        if self.provider_type == 'bifrost':
            if self.api_key:
                headers['Authorization'] = f'Bearer {self.api_key}'
            else:
                headers['X-Virtual-Key'] = self.env['ir.config_parameter'].get_param(
                    'bifrost.admin_api_key', '')
        elif self.api_key:
            headers['Authorization'] = f'Bearer {self.api_key}'
        url = self.base_url.rstrip('/') + '/config'
        try:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=30, context=ctx) as resp:
                data = json.loads(resp.read().decode())
        except Exception as e:
            _logger.debug("No /v1/config catalog for %s: %s", self.name, e)
            return {}
        models = data.get('models', data.get('data', data if isinstance(data, list) else []))
        out = {}
        for m in models:
            mid = m.get('id') or m.get('name')
            if not mid:
                continue
            cost_in = None
            cost_out = None
            cost = m.get('cost') or {}
            if isinstance(cost, dict):
                if cost.get('input') is not None:
                    cost_in = float(cost['input'])
                if cost.get('output') is not None:
                    cost_out = float(cost['output'])
            out[mid] = {
                'context': m.get('contextWindow'),
                'max_output': m.get('maxTokens') or m.get('max_output_tokens'),
                'cost_input': cost_in,
                'cost_output': cost_out,
            }
        return out

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

    def _import_model(self, model_id: str, config_meta: Optional[dict] = None):
        """Create or update ai.model from provider data (kanonicaliserat).

        - name = kanoniskt identitetsnamn (prefix-strippat)
        - api_name = full path (wire-id)
        - source_provider = tillverkare (gateway) / tom (direkt)
        - find-or-update på (name, provider) — UNIQUE-safe, idempotent
        - context_window / max_output_tokens / kostnad: berikas från
          config_meta (rik /v1/config-katalog) när tillgänglig.
        For NEW models: sets default sys_multiplier based on model name heuristics.
        For EXISTING models: preserves manually set context/pris (endast
          kvantiteter som admin ännu inte angivet — 0/NULL — fylls på).
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

        # Berika med providerns rika config (context/pris).
        # fix-context-sync: för Bifrost-gateway är /v1/config AUKTORITATIV
        # (katalogen ägs av combo-adaptern, inte admin här) — uppdatera alltid
        # context/max från config, inkl. 0. Direkta providers (OpenAI etc.)
        # behåller admin-manuella värden: bara fyll på 0/default.
        DEFAULT_CTX = 128000
        DEFAULT_MAXOUT = 16384
        if config_meta:
            ctx = config_meta.get('context')
            mo = config_meta.get('max_output')
            if self.provider_type == 'bifrost':
                if ctx is not None:
                    vals['context_window'] = int(ctx)
                if mo is not None:
                    vals['max_output_tokens'] = int(mo)
            else:
                if ctx:
                    cur = existing.context_window if existing else 0
                    if not cur or cur == DEFAULT_CTX:
                        vals['context_window'] = int(ctx)
                if mo:
                    cur = existing.max_output_tokens if existing else 0
                    if not cur or cur == DEFAULT_MAXOUT:
                        vals['max_output_tokens'] = int(mo)
            # Pris per token (adaptern) → per 1K-token (ai.model).
            ci = config_meta.get('cost_input')
            co = config_meta.get('cost_output')
            if ci is not None:
                if not existing or not existing.cost_input_1k:
                    vals['cost_input_1k'] = ci * 1000
                # Admin-insyn: provider $/1M-token (input).
                if not existing or not existing.provider_cost_1M:
                    vals['provider_cost_1M'] = ci * 1_000_000
            if co is not None:
                if not existing or not existing.cost_output_1k:
                    vals['cost_output_1k'] = co * 1000

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
            'views': [[False, 'list'], [False, 'kanban'], [False, 'form']],
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

    # ════════════════════════════════════════════
    # EMBEDDINGS (okf-recall-path, D2/D3)
    # ════════════════════════════════════════════
    # Dimensionen är en enda sanning och måste matcha kolumnen
    # ai_okf_concept.embedding = vector(1024). Skickas explicit till API:t
    # (text-embedding-3-small stödjer Matryoshka-trunkering). Att inte skicka
    # den gav 1536 dim mot en 1024-kolumn → tystnat i try/except → tom kolumn.

    DEFAULT_EMBEDDING_MODEL = 'text-embedding-3-small'

    # ── Embedding-kapabilitet (okf-recall-path §17, väg 2) ──────────────
    # Fältet är den ENDA sanningen om vilken provider som får embedda.
    # Bakgrund: sju anropsställen gjorde `search([('active','=',True)],
    # limit=1)` — ett obundet val som i drift landade på Anthropic (id 2),
    # vars /embeddings ger HTTP 404. Vektorn uteblev, `embedding_state`
    # stod kvar på 'pending' för alla 110 koncept, och den semantiska
    # signalen var tyst död medan BM25 såg ut att fungera.
    #
    # Ett fält (inte en lista i koden) för samma skäl som
    # `_known_backends()` i ai.search.source: två listor glider isär.
    can_embed = fields.Boolean(
        'Kan skapa embeddings',
        default=False,
        help='Providern får användas för att skapa vektorer. Sätts på den '
             'provider vars /embeddings faktiskt svarar (verifierat, inte '
             'antaget).',
    )
    embedding_model = fields.Char(
        'Embedding-modell',
        help='Modell-id som skickas till providern. Måste vara ett explicit '
             'modell-id — Bifrost avvisar generativa kombomodeller med '
             '"Generative combos cannot be used for embeddings".',
    )
    embedding_dim = fields.Integer(
        'Embedding-dimension',
        help='Dimensionen providern returnerar. Måste matcha kolumnen '
             'ai_okf_concept.embedding = vector(1024).',
    )

    @api.model
    def _embedding_provider(self):
        """Den provider som ska skapa vektorer — en enda sanning.

        Ersätter sju obundna `search([('active','=',True)], limit=1)`.
        Ordningen är avsiktlig:

          1. En aktiv provider som uttryckligen markerats `can_embed`.
          2. En aktiv bifrost-provider med nyckel (Bifrost är den enda
             gatewayen; en tom nyckel ger 401, inte en vektor).
          3. Ingen — anroparen ska då hoppa över vektorn och säga varför.

        Att returnera en provider som saknar `can_embed` vore att upprepa
        ursprungsbuggen: en provider som 'finns' men inte kan embedda.
        """
        Provider = self.env['ai.provider'].sudo()
        explicit = Provider.search([
            ('active', '=', True), ('can_embed', '=', True)], limit=1)
        if explicit:
            return explicit
        # Fallback: bifrost med nyckel. `api_key` kan vara tom på raden även
        # när gatewayen fungerar (nyckeln kan komma från ir.config_parameter)
        # — därför kontrolleras båda innan vi ger upp.
        admin = self.env['ir.config_parameter'].sudo().get_param(
            'bifrost.admin_api_key', '')
        for cand in Provider.search([
                ('active', '=', True), ('provider_type', '=', 'bifrost')]):
            if cand.api_key or admin:
                return cand
        return Provider.browse()

    # Fältet vinner över konstanten: DEFAULT_EMBEDDING_MODEL pekar på
    # 'text-embedding-3-small', som Bifrost avvisar (000/401). Att låta
    # konstanten vinna hade gjort inställningen till dekoration.
    def _effective_embedding_model(self, model=None):
        """Modell-id med rätt prioritet: argument → fält → konstant."""
        self.ensure_one()
        return model or self.embedding_model or self.DEFAULT_EMBEDDING_MODEL

    def _effective_embedding_dim(self):
        """Dimension: providerns eget fält om satt, annars kolumnens."""
        self.ensure_one()
        return self.embedding_dim or self._embedding_dim()

    def _embedding_dim(self):
        """Kolumnens dimension — importeras från OKF för en enda sanning."""
        self.ensure_one()
        from .ai_okf_concept import EMBEDDING_DIM
        return EMBEDDING_DIM

    def _embedding_headers(self):
        """HTTP-headers för embeddings — gatewayens riktiga protokoll.

        Bifrost kräver `x-bf-vk` (virtual key) för /embeddings. Den headern
        används av den fungerande Pi-klienten (`~/.pi/agent/extensions/
        bifrost.ts:89`) och är den gatewayen faktiskt svarar på.

        HISTORIK: koden skickade tidigare `X-Virtual-Key` — en header
        gatewayen accepterar men ignorerar. Det gav inte 401 utan ett tyst
        avbrott, vilket är precis det mönster detta change arbetar bort.
        `x-bf-vk` är det namn som bevisats fungera.

        VK:n hämtas ur providerns `api_key`, med fallback till
        `bifrost.admin_api_key` (ir.config_parameter).
        """
        self.ensure_one()
        headers = {'Content-Type': 'application/json'}
        vk = self.api_key or self.env['ir.config_parameter'].get_param(
            'bifrost.admin_api_key', '') or ''
        if self.provider_type == 'bifrost':
            headers['x-bf-vk'] = vk
        elif self.api_key:
            headers['Authorization'] = 'Bearer %s' % self.api_key
        return headers

    def _embedding_endpoint(self):
        self.ensure_one()
        base = (self.base_url or '').rstrip('/')
        return base + '/embeddings' if base else ''

    def _embedding_post(self, url, payload, headers, timeout=None):
        """Utför HTTP-anropet och returnera det tolkade svaret.

        Egen metod av två skäl:
          1. Testbarhet — Odoo:s testramverk blockerar extern HTTP, så
             anropet måste kunna mockas på en nivå som ÄR koden.
          2. Ett ställe för timeout/headers/retry — annars glider de isär
             mellan singel- och batch-vägen.

        RETRY (mätt 2026-09-14): Bifrost svarar normalt på 0,2–0,5 s men
        hänger intermittently i exakt samma anrop. Uppmätt över 6 anrop
        från Odoo: 4 svarade på 0,3–0,4 s, 2 hängde till timeout. Samma
        payload, samma header, samma sekund — alltså är det inte vår
        request det är fel på. Utan retry förlorar en tredjedel av alla
        koncept sin vektor till en övergående hängning.

        Därför: kort timeout per försök + retry. En hängning kostar då
        ~3×timeout i värsta fall i stället för att blockera 120 s per
        post (110 poster × 120 s = 3,7 timmar för en cron-körning).

        Kastar vidare nätverksfel till anroparen, som loggar och ger None.
        """
        import requests

        attempts = self._embedding_retry_attempts()
        per_try = timeout or self._embedding_timeout()
        last_error = None

        for attempt in range(1, attempts + 1):
            try:
                resp = requests.post(
                    url,
                    json=payload,
                    headers=headers,
                    timeout=per_try,
                )
                if resp.status_code != 200:
                    raise ValueError('HTTP %s: %s' % (
                        resp.status_code, resp.text[:200]))
                return resp.json()
            except Exception as e:  # requests.RequestException + ValueError
                last_error = e
                if attempt < attempts:
                    _logger.info(
                        'Embedding: försök %s/%s misslyckades (%s) — '
                        'försöker igen', attempt, attempts, str(e)[:120])
                    time.sleep(self._embedding_retry_delay(attempt))

        raise last_error

    def _embedding_timeout(self):
        """Timeout per försök. Kortare än providerns generella timeout.

        Providerns `timeout` är 120 s (rätt för LLM-svar, som kan ta lång
        tid). En embedding ska svara på under en sekund — att vänta 120 s
        på något som antingen kommer direkt eller aldrig kommer är att
        göra en övergående hängning till ett dygnsproblem.
        """
        self.ensure_one()
        return min(self.timeout or 120, 20)

    def _embedding_retry_attempts(self):
        """Antal försök totalt (inte antal omförsök)."""
        self.ensure_one()
        return 3

    def _embedding_retry_delay(self, attempt):
        """Linjär backoff i tiondelar — gatewayen behöver ingen lång vila."""
        self.ensure_one()
        return 0.5 * attempt

    def _embedding_payload(self, model, inputs, dim, input_type=None):
        """Payload för /embeddings — med de fält gatewayen KRÄVER.

        `input_type` är inte valfritt för alla modeller. Mätt mot Bifrost
        (2026-09-14):

          embed-multilingual-v3.0 + input_type  → HTTP 200 på 0,5 s
          embed-multilingual-v3.0 UTAN input_type → HTTP 000 efter 45 s

        Det är den farligaste sortens fel: ingen felkod, bara en tyst
        timeout. `_get_embedding` fångar den och returnerar None, så
        vektorn försvinner utan spår i loggen utom den generiska
        "Embedding-fel". Att utelämna fältet är därför inte ett alternativ
        — men att ALLTID skicka det är inte heller rätt: OpenAI-modeller
        avvisar okända fält. Därför skickas `input_type` bara när modellen
        känner igen det (Cohere-familjen och andra som deklarerar det).

        input_type-semantiken (fråga vs dokument) är inte kosmetisk:
        asymmetriska embeddingsmodeller lägger frågor och dokument i olika
        delar av rummet. Att embedda en sökfråga som ett dokument ger
        sämre träff — och det syns inte som ett fel, bara som sämre svar.

        Args:
            model: modell-id
            inputs: str eller list[str]
            dim: dimensionen att begära
            input_type: 'search_query' | 'search_document' | None

        Returns:
            dict — redo att JSON-kodas
        """
        self.ensure_one()
        payload = {'model': model, 'input': inputs}
        # Dimensionen skickas bara till modeller som stödjer trunkering.
        # embed-multilingual-v3.0 hänger på dimensions=512 → skicka bara
        # den dimension vi faktiskt vill ha (1024 = kolumnens).
        if dim:
            payload['dimensions'] = dim
        if input_type and self._accepts_input_type(model):
            payload['input_type'] = input_type
        return payload

    # Modeller som kräver input_type. Listan är avsiktligt kort och explicit:
    # ett fält som heter samma sak hos flera leverantörer betyder inte samma
    # sak, och att gissa ger tysta 45-sekunderstimeouter (se ovan).
    _INPUT_TYPE_MODELS = ('embed-multilingual', 'embed-english', 'embed-v3',
                          'cohere')

    def _accepts_input_type(self, model):
        """Kräver modellen `input_type`? (mätt, inte antaget)"""
        self.ensure_one()
        name = (model or '').lower()
        return any(k in name for k in self._INPUT_TYPE_MODELS)

    def _get_embedding(self, model=None, input=None, input_type=None):
        """Embedda EN text → rå lista av float (D2).

        Returnerar list[float] med exakt `EMBEDDING_DIM` element, eller None
        om ingen embedding kunde skapas. Anroparen får aldrig en tyst None
        utan att orsaken loggats på `warning`-nivå.

        Args:
            model: embeddingsmodell (default: providerns fält, annars
                DEFAULT_EMBEDDING_MODEL)
            input: texten att embedda
            input_type: 'search_query' när texten är en SÖKFRÅGA,
                'search_document' när den är ett DOKUMENT som ska hittas.
                Anroparen vet vilket — providern kan inte gissa.

        Returns:
            list[float] | None
        """
        self.ensure_one()
        if input is None or not str(input).strip():
            _logger.warning(
                'Embedding: tom input (provider=%s)', self.name)
            return None

        url = self._embedding_endpoint()
        if not url:
            _logger.warning(
                'Embedding: provider %s saknar base_url — kan inte skapa '
                'vektorer (semantisk sökning körs inte)', self.name)
            return None

        model = self._effective_embedding_model(model)
        dim = self._effective_embedding_dim()
        try:
            data = self._embedding_post(
                url,
                self._embedding_payload(
                    model, str(input)[:8192], dim, input_type),
                self._embedding_headers(),
            )
            vector = data['data'][0]['embedding']
        except Exception as e:
            _logger.warning(
                'Embedding-fel (provider=%s, modell=%s): %s',
                self.name, model, e)
            return None

        return self._validate_embedding(vector, model, dim)

    def _get_embedding_batch(self, model=None, inputs=None, input_type=None):
        """Embedda flera texter → lista av råa float-listor (D2).

        Anropas av ai_personal_memory.py:828 och ai_memory_mixin.py:285.

        Args:
            model: embeddingsmodell (default: providerns fält, annars
                DEFAULT_EMBEDDING_MODEL)
            inputs: lista av texter
            input_type: 'search_query' | 'search_document' — se
                `_get_embedding`. En batch är i praktiken alltid
                'search_document' (minnen som ska indexeras), men fältet
                skickas vidare så att anroparen bestämmer.

        Returns:
            list[list[float] | None] — en post per input, i samma ordning
        """
        self.ensure_one()
        if not inputs:
            return []

        url = self._embedding_endpoint()
        if not url:
            _logger.warning(
                'Embedding (batch): provider %s saknar base_url', self.name)
            return [None] * len(inputs)

        model = self._effective_embedding_model(model)
        dim = self._effective_embedding_dim()
        truncated = [str(t)[:8192] for t in inputs]
        try:
            data = self._embedding_post(
                url,
                self._embedding_payload(model, truncated, dim, input_type),
                self._embedding_headers(),
            )
            # API:t returnerar {index, embedding} — sortera på index så att
            # ordningen matchar anroparens lista (kontraktet: samma ordning).
            rows = sorted(data.get('data', []),
                          key=lambda d: d.get('index', 0))
            if len(rows) != len(inputs):
                _logger.warning(
                    'Embedding (batch): fick %s vektorer för %s texter '
                    '(provider=%s) — alla avvisas (hellre tomt än felkopplat)',
                    len(rows), len(inputs), self.name)
                return [None] * len(inputs)
            return [self._validate_embedding(r.get('embedding'), model, dim)
                    for r in rows]
        except Exception as e:
            _logger.warning(
                'Embedding-fel (batch, provider=%s, modell=%s): %s',
                self.name, model, e)
            return [None] * len(inputs)

    def _validate_embedding(self, vector, model, dim):
        """Längdvalidering FÖRE INSERT (D3 / okf-recall krav 2).

        En vektor med fel dimension avvisas och loggas —
        `expected 1024 dimensions, not 1536` ska aldrig kunna uppstå vid
        INSERT. Returnerar list[float] eller None.
        """
        if not vector:
            _logger.warning(
                'Embedding: tom vektor (provider=%s, modell=%s)',
                self.name, model)
            return None
        try:
            values = [float(x) for x in vector]
        except (TypeError, ValueError) as e:
            _logger.warning(
                'Embedding: icke-numerisk vektor (provider=%s, modell=%s): %s',
                self.name, model, e)
            return None
        if len(values) != dim:
            _logger.warning(
                'Embedding: fel dimension — fick %s, förväntade %s '
                '(provider=%s, modell=%s). Vektorn avvisas; konceptet sparas '
                'utan embedding.',
                len(values), dim, self.name, model)
            return None
        return values


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
            'views': [[False, 'form']],
            'target': 'current',
        }
