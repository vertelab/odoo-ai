"""Tester för agent-model-resolution.

Mätt i drift 2026-09-18: 20 av 21 agenter saknade `model_id`, och
systemparametern `ai_agent_core.default_model_id` existerade inte.

Kedjan ai.coworker → agent → ai.model → ai.provider är den enda vägen till
en LLM. Utan modell returnerade `resolve_provider_from_coworker()` (None,
None), och körningen kraschade först vid `await provider.aclose()` — efter
att sessionen skapats. Resultatet var en tom session med status='error'.
"""
from odoo.tests import tagged
from ._config_param_guard import ConfigParamGuardedCase
from odoo.exceptions import ValidationError


@tagged('post_install', '-at_install')
class TestDefaultModel(ConfigParamGuardedCase):

    def setUp(self):
        super().setUp()
        self.param = 'ai_agent_core.default_model_id'
        self.Model = self.env['ai.model']

    def test_ensure_default_model_sets_parameter(self):
        """Parametern ska sättas av _ensure_default_model."""
        from odoo.addons.ai_agent_core.hooks import _ensure_default_model

        self.env['ir.config_parameter'].sudo().search(
            [('key', '=', self.param)]).unlink()
        _ensure_default_model(self.env)

        value = self.env['ir.config_parameter'].sudo().get_param(self.param)
        self.assertTrue(value, 'default_model_id sattes inte')
        self.assertTrue(self.Model.browse(int(value)).exists())

    def test_ensure_default_model_is_idempotent(self):
        """Andra körningen ska behålla samma modell."""
        from odoo.addons.ai_agent_core.hooks import _ensure_default_model

        _ensure_default_model(self.env)
        first = self.env['ir.config_parameter'].sudo().get_param(self.param)
        _ensure_default_model(self.env)
        second = self.env['ir.config_parameter'].sudo().get_param(self.param)
        self.assertEqual(first, second)

    def test_get_default_provider_without_request(self):
        """Uppslagningen ska fungera utan HTTP-request (cron, shell).

        Tidigare krävde get_default_provider() en request och returnerade
        därför alltid (None, None) i cron — den vanligaste vägen in.
        """
        from odoo.addons.ai_agent_core.core.provider import get_default_provider
        from odoo.addons.ai_agent_core.hooks import _ensure_default_model

        # Testdatabasen kör inte post_init — sätt parametern själv.
        _ensure_default_model(self.env)

        provider, model = get_default_provider(self.env)
        self.assertTrue(provider, 'ingen provider utan request')
        self.assertTrue(model, 'ingen modell utan request')

    def test_get_default_provider_missing_param(self):
        """Utan parameter ska (None, None) returneras — inte krascha."""
        from odoo.addons.ai_agent_core.core.provider import get_default_provider

        self.env['ir.config_parameter'].sudo().search(
            [('key', '=', self.param)]).unlink()
        provider, model = get_default_provider(self.env)
        self.assertFalse(provider)
        self.assertFalse(model)


@tagged('post_install', '-at_install')
class TestProviderPrecheck(ConfigParamGuardedCase):

    def setUp(self):
        super().setUp()
        self.Coworker = self.env['ai.coworker']

    def test_repair_gives_model_to_agents_without(self):
        """Agenter utan modell ska få default-modellen."""
        from odoo.addons.ai_agent_core.hooks import _ensure_default_model
        _ensure_default_model(self.env)

        agent = self.env['ai.agent'].create({
            'name': 'Testagent utan modell',
            'ai_role': 'Test',
        })
        self.assertFalse(agent.model_id)

        self.Coworker._repair_missing_agent_models()
        self.assertTrue(agent.model_id, 'agenten fick ingen modell')

    def test_repair_does_not_touch_existing_model(self):
        """En agent med ett medvetet val ska inte skrivas över."""
        from odoo.addons.ai_agent_core.hooks import _ensure_default_model
        _ensure_default_model(self.env)

        models = self.env['ai.model'].search([('active', '=', True)], limit=2)
        if len(models) < 2:
            self.skipTest('behöver två modeller')

        agent = self.env['ai.agent'].create({
            'name': 'Testagent med modell',
            'ai_role': 'Test',
            'model_id': models[1].id,
        })

        self.Coworker._repair_missing_agent_models()
        self.assertEqual(agent.model_id, models[1],
                         'reparationen skrev över ett medvetet val')

    def test_repair_is_idempotent(self):
        """Andra körningen ska reparera 0."""
        from odoo.addons.ai_agent_core.hooks import _ensure_default_model
        _ensure_default_model(self.env)

        agent = self.env['ai.agent'].create({
            'name': 'Testagent idempotent',
            'ai_role': 'Test',
        })
        first = self.Coworker._repair_missing_agent_models()
        self.assertGreaterEqual(first, 1)
        second = self.Coworker._repair_missing_agent_models()
        self.assertEqual(second, 0, 'reparationen var inte idempotent')
        self.assertTrue(agent.model_id)

    def test_repair_without_param_returns_zero(self):
        """Utan default_model_id ska reparationen avböja — inte krascha."""
        self.env['ir.config_parameter'].sudo().search(
            [('key', '=', 'ai_agent_core.default_model_id')]).unlink()
        self.assertEqual(self.Coworker._repair_missing_agent_models(), 0)

    def test_resolve_provider_or_raise_without_model(self):
        """En coworker utan modell ska ge ett fel som NAMNGER modellen.

        Inte 'NoneType' object has no attribute 'aclose' — det pekade på
        fel sak.
        """
        self.env['ir.config_parameter'].sudo().search(
            [('key', '=', 'ai_agent_core.default_model_id')]).unlink()

        coworker = self.Coworker.create({
            'name': 'Testcoworker utan modell',
            'description': 'Test',
            'init_type': 'manual',
            'status': 'active',
        })
        agent = self.env['ai.agent'].create({
            'name': 'Testagent utan modell 2',
            'ai_role': 'Test',
        })
        self.env['ai.coworker.agent'].create({
            'coworker_id': coworker.id,
            'agent_id': agent.id,
            'role': 'leader',
        })

        with self.assertRaises(ValidationError) as ctx:
            coworker._resolve_provider_or_raise()

        msg = str(ctx.exception)
        self.assertIn('model_id', msg,
                      'felmeddelandet namnger inte model_id')
        self.assertNotIn('aclose', msg,
                         'felmeddelandet pekar fortfarande på aclose')

    def test_resolve_provider_or_raise_with_model(self):
        """En coworker med modell ska ge en provider."""
        from odoo.addons.ai_agent_core.hooks import _ensure_default_model
        _ensure_default_model(self.env)

        coworker = self.Coworker.create({
            'name': 'Testcoworker med modell',
            'description': 'Test',
            'init_type': 'manual',
            'status': 'active',
        })
        agent = self.env['ai.agent'].create({
            'name': 'Testagent med modell 2',
            'ai_role': 'Test',
        })
        self.env['ai.coworker.agent'].create({
            'coworker_id': coworker.id,
            'agent_id': agent.id,
            'role': 'leader',
        })
        self.Coworker._repair_missing_agent_models()

        provider, model = coworker._resolve_provider_or_raise()
        self.assertTrue(provider)
        self.assertTrue(model)
