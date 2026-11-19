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

    def _check_against_gateway_catalog(self):
        """Varna (aldrig blockera) om api_name saknas i gateway-katalogen.

        Icke-blockerande av design: gatewayen kan vara tillfälligt nere, och
        ett hårt stopp skulle göra konfiguration omöjlig under underhåll.
        Otillhämtad katalog = ingen varning, bara en notering i loggen.
        """
        self.ensure_one()
        provider = self.provider
        if not provider or not provider._is_gateway():
            return
        name = self.api_name or self.name
        if not name:
            return
        names, ok = provider._fetch_catalog_names()
        if not ok:
            _logger.info(
                'Model validation: the catalog for %s could not be fetched — the check was not performed for \'%s\'.', provider.name, name)
            return
        if name not in set(names):
            _logger.warning(
                "Model validation: '%s' is not in the gateway %s catalog (the registry and the catalog have drifted apart).",
                name, provider.name)

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        # Icke-blockerande: varna efter skapandet, spara alltid.
        for rec in records:
            try:
                rec._check_against_gateway_catalog()
            except Exception as e:  # validering får aldrig fälla create
                _logger.info('Modell-validering misslyckades: %s', e)
        return records

    def write(self, vals):
        res = super().write(vals)
        if 'api_name' in vals or 'name' in vals or 'provider' in vals:
            for rec in self:
                try:
                    rec._check_against_gateway_catalog()
                except Exception as e:
                    _logger.info('Modell-validering misslyckades: %s', e)
        return res

    def _get_maker(self):
        """Tillverkaren: source_provider om satt, annars provider (direktkoppling)."""
        self.ensure_one()
        return self.source_provider or self.provider

    @api.model
    def _resolve_from_real(self, model_real, quest=None):
        """Find ai.model from a wire/display model name — kanal-medvetet.

        Föredrar modeller från coworkerns agenter (record-id), sedan exakt
        match på api_name/name. Aldrig ilike+limit=1 på name ensamt —
        kanoniskt namn delas av flera record över kanaler.
        """
        if not model_real:
            return self.browse()
        if quest and quest.agent_ids:
            matched = quest.agent_ids.agent_id.model_id.filtered(
                lambda m: m and (m.name == model_real or m.api_name == model_real))
            if matched:
                return matched[:1]
        return self.search([
            '|', ('name', '=', model_real), ('api_name', '=', model_real),
        ], limit=1)

    @api.depends('source_provider.name', 'provider.name')
    def _compute_real_provider(self):
        """Real provider = tillverkaren (source_provider eller provider)."""
        for r in self:
            maker = r.source_provider or r.provider
            r.real_provider = maker.name if maker else ''

    # ── Systemtoken-kalibrering (systemtoken-calibration D1/D2) ─────────

    @api.model
    def _calibration_params(self):
        """Systemtoken-priset och vikterna — EN källa för beräkningen.

        Samma nycklar och samma defaults som
        `bifrost.valuation._pricing_params()` läser, så de två sidorna av
        bryggan inte kan glida isär. `ai_agent_core` importerar INTE
        `bifrost` — formeln är tre rader aritmetik på två fält som redan
        finns här, och att duplicera *aritmetiken* är billigare än att
        skapa ett modulberoende.

        Returnerar ``(price, w_in, w_out)``.
        """
        icp = self.env['ir.config_parameter'].sudo()

        def _f(key, default):
            try:
                return float(icp.get_param(key) or default)
            except (TypeError, ValueError):
                return float(default)

        price = _f('bifrost.systemtoken_price',
                   _f('bifrost.systemtoken_price_sek', 35))
        return (
            price,
            _f('bifrost.systemtoken_input_weight', 0.25),
            _f('bifrost.systemtoken_output_weight', 0.75),
        )

    def _calibration_cost_per_1m(self, w_in, w_out, date=None):
        """Viktad modellkostnad per 1M tokens, i BOLAGETS valuta.

        Samma formel som `bifrost.valuation._blended_price_company`:
        ``(input_pris × w_in + output_pris × w_out) × 1_000_000``.

        `cost_input_1k`/`cost_output_1k` är per 1K-token, så ×1000 ger per
        1M. Priset står i modellens `pricing_currency_id` (vanligen USD)
        medan systemtoken-priset är i bolagsvalutan — utan konvertering
        blir faktorn 10× fel när USD/SEK är ~10.

        Konverteringen sker här, inte i `_compute_sys_multiplier`, så att
        `calibration_cost_per_1m` alltid är i bolagsvaluta och direkt
        jämförbart med `calibration_price`.

        Saknas kursen returneras 0.0 — att räkna med kurs 1.0 vore att
        tysta felet i stället för att visa det.
        """
        self.ensure_one()
        amount = (
            (self.cost_input_1k or 0.0) * w_in
            + (self.cost_output_1k or 0.0) * w_out
        ) * 1000.0
        if not amount:
            return 0.0

        company = self.env.company
        company_currency = company.currency_id
        pricing_currency = (self.pricing_currency_id
                            or self.env.ref('base.USD',
                                            raise_if_not_found=False))
        if not pricing_currency or not company_currency:
            return amount
        if pricing_currency.id == company_currency.id:
            return amount

        try:
            return pricing_currency._convert(
                amount, company_currency, company,
                date or fields.Date.context_today(self))
        except Exception as exc:  # noqa: BLE001 — kursen kan saknas
            _logger.warning(
                'sys_multiplier: kunde inte konvertera %s %s till %s för '
                'modell %s (%s) — faktorn blir 0.0',
                amount, pricing_currency.name, company_currency.name,
                self.display_name or self.name, exc)
            return 0.0

    @api.depends('cost_input_1k', 'cost_output_1k', 'pricing_currency_id')
    def _compute_sys_multiplier(self):
        """Systemtokens per riktig token — ur priset, aldrig ur namnet.

            kostnad_per_1M = (cost_input_1k × w_in + cost_output_1k × w_out) × 1000
            sys_multiplier = kostnad_per_1M / systemtoken_pris

        En billig modell ger en LITEN faktor (få systemtokens per riktig
        token); en dyr ger en stor. Det är motsatsen till Bifrosts
        `systemtokens_per_block` (pris / kostnad), som svarar på hur många
        systemtokens en miljon riktiga tokens är värd — de två är varandras
        inverterade, vilket är avsiktligt.

        Pris 0 ger faktor 0.0 och en varning: att gissa ett pris vore värre
        än att visa att det saknas.
        """
        price, w_in, w_out = self._calibration_params()
        today = fields.Date.context_today(self)
        for r in self:
            cost_per_1m = r._calibration_cost_per_1m(w_in, w_out, date=today)
            if not price:
                r.sys_multiplier = 0.0
                _logger.warning(
                    'sys_multiplier: systemtoken-priset är 0 — modell %s '
                    'får faktor 0.0', r.display_name or r.name)
            elif not cost_per_1m:
                r.sys_multiplier = 0.0
                _logger.warning(
                    'sys_multiplier: modell %s saknar pris '
                    '(cost_input_1k=0, cost_output_1k=0) — faktor 0.0',
                    r.display_name or r.name)
            else:
                r.sys_multiplier = cost_per_1m / price

    @api.model
    def _cron_recalculate_calibration(self):
        """Månadsvis omräkning av `sys_multiplier` (systemtoken-calibration D2).

        Körs vid månadsskifte så att en prisändring hos leverantören slår
        igenom i stället för att aldrig slå igenom. Idempotent: samma månad
        ger samma faktor.

        Faktorn fryses på varje session line vid skapande — rader från
        föregående månad behåller sin sats.
        """
        from datetime import date
        today = date.today()
        month = today.strftime('%Y-%m')
        price, w_in, w_out = self._calibration_params()
        models = self.search([])
        changed = 0
        for m in models:
            before = m.sys_multiplier
            # Samma datum för hela körningen — annars kan två modeller i
            # samma månad räknas med olika kurs om cronen går över midnatt.
            cost_per_1m = m._calibration_cost_per_1m(w_in, w_out, date=today)
            m.write({
                'calibration_month': month,
                'calibration_price': price,
                'calibration_cost_per_1m': cost_per_1m,
            })
            if abs((m.sys_multiplier or 0.0) - (before or 0.0)) > 1e-12:
                changed += 1
        _logger.info(
            'systemtoken-kalibrering %s: %d modeller omräknade, %d ändrade '
            '(pris=%s, w_in=%s, w_out=%s)',
            month, len(models), changed, price, w_in, w_out)
        return changed

    # Capabilities
    is_vision = fields.Boolean('Vision', help='Supports image input')
    is_embedded = fields.Boolean('Embedding', help='Supports text embeddings')
    is_asr = fields.Boolean('Speech-to-Text', help='Supports audio transcription')
    is_text2image = fields.Boolean('Text-to-Image', help='Supports image generation')
    is_tts = fields.Boolean('Text-to-Speech', help='Supports audio output / TTS')
    is_video_input = fields.Boolean('Video Input', help='Supports video input')
    is_pdf_input = fields.Boolean('PDF Input', help='Supports PDF document input')
    is_multimodal = fields.Boolean('Multimodal', help='Supports mixed text/image/audio/video in one prompt')
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
    pricing_currency_id = fields.Many2one(
        'res.currency', string='Pricing Currency',
        default=lambda self: self.env.ref('base.USD',
                                          raise_if_not_found=False),
        help='Currency the cost_input_1k/cost_output_1k prices are stated '
             'in. Usually USD. Converted to the company currency when the '
             'systemtoken factor is calculated — without that conversion '
             'the factor is off by the exchange rate.')

    # Systemtoken pricing (beräknad — systemtoken-calibration D1/D2)
    #
    # Faktorn är INTE en åsikt: den härleds ur modellens faktiska pris och
    # systemtoken-priset. En riktig token för en billig modell är värd få
    # systemtokens; för en dyr modell många.
    #
    #   kostnad_per_1M = (cost_input_1k x w_in + cost_output_1k x w_out) x 1000
    #   sys_multiplier = kostnad_per_1M / systemtoken_pris
    #
    # `systemtoken_pris` läses ur `bifrost.systemtoken_price` (fallback
    # `bifrost.systemtoken_price_sek`, default 35). Vikterna ur
    # `bifrost.systemtoken_input_weight` (0.25) och
    # `bifrost.systemtoken_output_weight` (0.75) — samma parametrar som
    # `bifrost.valuation` använder, så de två sidorna av bryggan inte kan
    # glida isär. Faktorn räknas om en gång per månad (cron) och fryses på
    # varje session line vid skapande.
    sys_multiplier = fields.Float(
        'Systemtoken-multiplikator', compute='_compute_sys_multiplier',
        store=True, readonly=True, digits=(16, 8),
        help='Systemtokens per real token for this model. Derived from the '
             'model price and the systemtoken price — never from the model '
             'name. Frozen on each session line when it is created.')
    calibration_month = fields.Char(
        'Calibration Month', readonly=True,
        help='YYYY-MM of the month the multiplier was calculated for.')
    calibration_cost_per_1m = fields.Float(
        'Cost per 1M (company currency)', readonly=True, digits=(16, 6),
        help='The blended model price per 1M tokens, in company currency, '
             'that the multiplier was derived from.')
    calibration_price = fields.Float(
        'Systemtoken Price', readonly=True, digits=(16, 4),
        help='The systemtoken price (company currency per 1M systemtokens) '
             'used in the calculation.')
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
    session_tokens_last_30d = fields.Integer(
        compute='_compute_session_tokens_30d', string='Tokens (month)')

    @api.depends('name', 'api_name')
    def _compute_session_tokens_30d(self):
        for r in self:
            r.session_tokens_last_30d = self.env['ai.coworker.session.line']._tokens_this_month(
                extra=['|', ('model_real', '=', r.name),
                                ('model_real', '=', r.api_name)])

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
            r.llm_count = self.env['ai.agent'].search_count([('model_id', '=', r.id)])

    def _compute_session_line_count(self):
        for r in self:
            r.session_line_count = self.env['ai.coworker.session.line'].search_count([
                '|', ('model_real', '=', r.name), ('model_real', '=', r.api_name),
            ])

    def action_open_llms(self):
        """Open agents using this model (stat button)."""
        return {
            'name': 'Agenter',
            'type': 'ir.actions.act_window',
            'res_model': 'ai.agent',
            'view_mode': 'list,form',
            'views': [[False, 'list'], [False, 'form']],
            'domain': [('model_id', '=', self.id)],
        }

    def action_open_session_lines(self):
        """Open coworker session lines using this model (stat button)."""
        return {
            'name': 'Sessioner',
            'type': 'ir.actions.act_window',
            'res_model': 'ai.coworker.session.line',
            'view_mode': 'list,form',
            'views': [[False, 'list'], [False, 'form']],
            'domain': ['|', ('model_real', '=', self.name),
                       ('model_real', '=', self.api_name)],
        }
