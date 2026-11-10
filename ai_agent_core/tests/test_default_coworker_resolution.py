# -*- coding: utf-8 -*-
"""Tester för default-medarbetaren som provider-källa.

Täcker default-coworker-som-provider:
- 5.1  Default Coworker-fältet i res.config.settings (och att de borttagna
       fälten är borta)
- 5.5  resolve_coworker_or_default() — kedjan och att den aldrig kastar
- 5.6  get_default_model_name() returnerar alltid en sträng
"""



from odoo.addons.ai_agent_core.tests._config_param_guard import (
    ConfigParamGuardedCase,
)


class TestDefaultCoworkerSettings(ConfigParamGuardedCase):
    """5.1 — fältet i inställningarna."""

    def test_borttagna_falt_finns_inte(self):
        """Default Provider och Default Model ska vara borta ur settings."""
        Settings = self.env['res.config.settings']
        self.assertNotIn('ai_api_default_provider_id', Settings._fields)
        self.assertNotIn('ai_api_default_model_id', Settings._fields)

    def test_default_coworker_falt_finns(self):
        """Default Coworker-fältet finns och är kopplat till bolaget."""
        Settings = self.env['res.config.settings']
        self.assertIn('ai_default_coworker_id', Settings._fields)
        field = Settings._fields['ai_default_coworker_id']
        self.assertEqual(field.comodel_name, 'ai.coworker')
        # related='company_id.ai_default_coworker_id'
        self.assertIn('ai_default_coworker_id', str(field.related or ''))

    def test_valet_overlever_get_values(self):
        """Ett sparat val läses tillbaka via get_values() (task 2.4).

        Fältet är `related='company_id.ai_default_coworker_id'`, så
        persistensen sker på bolaget — inte i en config-parameter. Vi testar
        den vägen direkt i stället för via set_values(): i den här
        installationen kraschar set_values() i saltstack-modulen
        (`'_unknown' object has no attribute 'id'`), vilket är en FÖRBEFINTLIG
        bugg utanför denna ändring (verifierat: samma fel i
        TestDefaultToolIds med ändringens tester bortstashade).
        """
        coworker = self.env['ai.coworker'].create({
            'name': 'Test — settings-val',
            'status': 'active',
        })
        self.env.company.ai_default_coworker_id = coworker

        # Persistensen ligger på bolaget (related-fältet). Odoo cores
        # get_values() läser bara `config_parameter`-fält, så ett
        # related-fält syns INTE i den dict:en — det är förväntat.
        # Verifiera därför att ett nytt settings-record ser bolagets värde
        # (det är vad en omladdning av sidan gör).
        settings = self.env['res.config.settings'].new({})
        self.assertEqual(settings.ai_default_coworker_id, coworker)

        # Och att bolagsfältet är själva lagringen.
        self.assertEqual(
            self.env.company.ai_default_coworker_id, coworker)

    def test_ingen_config_parameter_for_default_coworker(self):
        """Default-medarbetaren lagras på bolaget, inte som config-parameter."""
        self.assertFalse(
            self.env['ir.config_parameter'].sudo().search([
                ('key', '=', 'ai_agent_core.default_coworker_id'),
            ], limit=1),
            'default_coworker_id ska inte finnas som config-parameter — '
            'den bor på res.company (D1)')

    def test_default_model_parametern_ror_s_inte_av_settings(self):
        """Inget settings-fält skriver ai_agent_core.default_model_id.

        Parametern ägs av Salt (ai_default_model) och reparationen — den är
        intern, inte en användarinställning. Vi verifierar att inget fält
        pekar på den (starkare än att köra set_values()).
        """
        Settings = self.env['res.config.settings']
        offenders = [
            name for name, field in Settings._fields.items()
            if (getattr(field, 'config_parameter', None) or '')
            == 'ai_agent_core.default_model_id'
        ]
        self.assertEqual(
            offenders, [],
            'Inget settings-fält får skriva ai_agent_core.default_model_id')


class TestResolveCoworkerOrDefault(ConfigParamGuardedCase):
    """5.5 — fallback-kedjan bevarar medarbetarens kontext."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.Provider = cls.env['ai.provider']
        cls.Model = cls.env['ai.model']
        cls.Coworker = cls.env['ai.coworker']
        cls.Agent = cls.env['ai.agent']

        cls.provider = cls.Provider.create({
            'name': 'Test-provider',
            'base_url': 'https://example.invalid',
            'api_key': 'test',
        })
        cls.model = cls.Model.create({
            'name': 'test-model',
            'api_name': 'test-model-wire',
            'provider': cls.provider.id,
            'status': 'active',
        })

    def setUp(self):
        super().setUp()
        self.company.ai_default_coworker_id = False

    def _coworker_with_model(self, name, model):
        coworker = self.Coworker.create({'name': name, 'status': 'active'})
        agent = self.Agent.create({
            'name': f'{name} — agent',
            'status': 'active',
            'model_id': model.id if model else False,
        })
        self.env['ai.coworker.agent'].create({
            'coworker_id': coworker.id,
            'agent_id': agent.id,
            'role': 'leader',
            'sequence': 10,
        })
        return coworker

    def test_egen_agent_modell_vinner(self):
        """Coworkerns egen agent-modell används när den finns."""
        from odoo.addons.ai_agent_core.core.provider import (
            resolve_coworker_or_default,
        )
        coworker = self._coworker_with_model('Egen modell', self.model)
        provider, model = resolve_coworker_or_default(coworker)
        self.assertTrue(provider)
        self.assertEqual(model, self.model)

    def test_fallback_till_default_medarbetaren(self):
        """Utan egen modell används default-medarbetarens modell."""
        from odoo.addons.ai_agent_core.core.provider import (
            resolve_coworker_or_default,
        )
        default_cw = self._coworker_with_model('Default', self.model)
        self.company.ai_default_coworker_id = default_cw
        utan_modell = self._coworker_with_model('Utan modell', None)

        provider, model = resolve_coworker_or_default(utan_modell)
        self.assertTrue(provider)
        self.assertEqual(model, self.model)

    def test_ingen_provider_nagonstans(self):
        """Utan coworker, default och parameter → (None, None)."""
        from odoo.addons.ai_agent_core.core.provider import (
            resolve_coworker_or_default,
        )
        self.env['ir.config_parameter'].sudo().set_param(
            'ai_agent_core.default_model_id', '')
        provider, model = resolve_coworker_or_default(
            self.Coworker.browse(), env=self.env)
        self.assertFalse(provider)
        self.assertFalse(model)

    def test_kastar_aldrig(self):
        """Helpern ska returnera (None, None), aldrig kasta."""
        from odoo.addons.ai_agent_core.core.provider import (
            resolve_coworker_or_default,
        )
        # Rensa parametern först — annars kan ett värde läcka in från ett
        # annat test (ir.config_parameter är ormcachad; se _config_param_guard)
        # och ge en provider, vilket gör testet falskt rött.
        self.env['ir.config_parameter'].sudo().set_param(
            'ai_agent_core.default_model_id', '')
        self.env.company.ai_default_coworker_id = False

        # En coworker som inte finns (tomt recordset) ska inte krascha.
        provider, model = resolve_coworker_or_default(
            self.Coworker.browse(999999), env=self.env)
        self.assertFalse(provider)
        self.assertFalse(model)


class TestDefaultModelName(ConfigParamGuardedCase):
    """5.6 — get_default_model_name() returnerar alltid en sträng."""

    def test_returnerar_strang_fran_medarbetaren(self):
        """Default-medarbetarens agent-modell ger api_name som sträng."""
        from odoo.addons.ai_agent_core.core.provider import (
            get_default_model_name,
        )
        provider = self.env['ai.provider'].create({
            'name': 'Test-provider 2',
            'base_url': 'https://example.invalid',
            'api_key': 'test',
        })
        model = self.env['ai.model'].create({
            'name': 'namn-modell',
            'api_name': 'wire-modell',
            'provider': provider.id,
            'status': 'active',
        })
        coworker = self.env['ai.coworker'].create({
            'name': 'Test — namn', 'status': 'active'})
        agent = self.env['ai.agent'].create({
            'name': 'Test — namn-agent', 'status': 'active',
            'model_id': model.id})
        self.env['ai.coworker.agent'].create({
            'coworker_id': coworker.id, 'agent_id': agent.id,
            'role': 'leader', 'sequence': 10})
        self.env.company.ai_default_coworker_id = coworker
        # Parametern måste vara tom, annars kan den vinna över medarbetaren
        # beroende på cachens tillstånd.
        self.env['ir.config_parameter'].sudo().set_param(
            'ai_agent_core.default_model_id', '')
        # Agentens modell måste vara läsbar via agent_ids[0].agent_id.model_id
        # — verifiera att kopplingen finns innan vi litar på resultatet.
        self.assertTrue(coworker.agent_ids, 'kopplingen skapades inte')
        self.assertEqual(
            coworker.agent_ids[0].agent_id.model_id, model,
            'agenten fick ingen modell')

        name = get_default_model_name(env=self.env)
        self.assertIsInstance(name, str)
        self.assertEqual(name, 'wire-modell')

    def test_tom_strang_utan_modell(self):
        """Utan modell någonstans returneras en tom sträng."""
        from odoo.addons.ai_agent_core.core.provider import (
            get_default_model_name,
        )
        self.env.company.ai_default_coworker_id = False
        self.env['ir.config_parameter'].sudo().set_param(
            'ai_agent_core.default_model_id', '')
        name = get_default_model_name(env=self.env)
        self.assertIsInstance(name, str)
        self.assertEqual(name, '')
