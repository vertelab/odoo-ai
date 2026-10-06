"""Regressionstester för Bifrost-nyckeluppslag i provider-resolutionen.

Bakgrund (drift 2026-09-15): `resolve_provider_from_model()` läste
`provider.api_key` rakt av. Bifrost-providern har ett TOMT api_key — den
virtuella nyckeln bor i `ir.config_parameter 'bifrost.admin_api_key'`. Utan
fallback skickades ingen X-Virtual-Key alls, och Bifrost svarade:

    403 Forbidden  {"error":{"message":"combo is not entitled for this
                    virtual key","type":"combo_forbidden"}}

Felet SER UT som en rättighetsfråga ("combo är inte tillåten för denna nyckel")
men är i själva verket en tom sträng. `ai.provider.fetch_models()` hade redan
fallbacken; LLM-anropsvägen hade den inte. Dessa tester pinnar att båda gör det.

Lardom: när ett felmeddelande namnger en ORSAK (entitlement), kontrollera att
den underliggande datan (nyckeln) ens finns. "Combo är inte tillåten" och
"ingen nyckel skickades" ger samma HTTP-status men kräver olika åtgärd.
"""
from odoo.tests import tagged
from ._config_param_guard import ConfigParamGuardedCase
from odoo.tests.common import TransactionCase


@tagged('post_install', '-at_install')
class TestBifrostKeyResolution(TransactionCase):
    """Pinnar fallbacken till bifrost.admin_api_key."""

    def setUp(self):
        super().setUp()
        self.ICP = self.env['ir.config_parameter'].sudo()
        self.admin_key = 'sk-bf-testadmin-0000-1111-2222-333344445555'
        self.ICP.set_param('bifrost.admin_api_key', self.admin_key)

        self.provider = self.env['ai.provider'].create({
            'name': 'Bifrost Test',
            'provider_type': 'bifrost',
            'base_url': 'http://127.0.0.1:8081/v1',
            'api_key': '',          # TOMT — det är hela poängen
            'is_bifrost': True,
        })
        self.model = self.env['ai.model'].create({
            'name': 'test-combo',
            'api_name': 'test-combo',
            'provider': self.provider.id,
        })

    def _resolve(self):
        from odoo.addons.ai_agent_core.core.provider import (
            resolve_provider_from_model,
        )
        return resolve_provider_from_model(self.model)

    def test_empty_bifrost_key_falls_back_to_admin_key(self):
        """Tomt api_key på en Bifrost-provider → admin-nyckeln används."""
        resolved = self._resolve()
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved.api_key, self.admin_key)
        self.assertTrue(resolved.is_bifrost)

    def test_own_key_wins_over_admin_key(self):
        """Egen nyckel på providern ska vinna över admin-nyckeln."""
        own = 'sk-bf-vk-egen-nyckel-aaaa-bbbb-cccc-ddddeeeeffff'
        self.provider.api_key = own
        resolved = self._resolve()
        self.assertEqual(resolved.api_key, own)

    def test_non_bifrost_provider_is_not_given_admin_key(self):
        """En icke-Bifrost-provider ska ALDRIG få Bifrost-admin-nyckeln."""
        other = self.env['ai.provider'].create({
            'name': 'OpenAI Test',
            'provider_type': 'openai',
            'base_url': 'https://api.openai.com/v1',
            'api_key': '',
            'is_bifrost': False,
        })
        model = self.env['ai.model'].create({
            'name': 'gpt-test',
            'api_name': 'gpt-test',
            'provider': other.id,
        })
        from odoo.addons.ai_agent_core.core.provider import (
            resolve_provider_from_model,
        )
        resolved = resolve_provider_from_model(model)
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved.api_key, '')
        self.assertNotEqual(resolved.api_key, self.admin_key)
        self.assertFalse(resolved.is_bifrost)

    def test_bifrost_header_is_x_virtual_key(self):
        """Bifrost-nyckeln ska hamna i X-Virtual-Key, inte Authorization."""
        import asyncio
        resolved = self._resolve()
        client = asyncio.run(resolved._get_client())
        try:
            self.assertEqual(client.headers.get('X-Virtual-Key'),
                             self.admin_key)
            self.assertNotIn('Authorization', client.headers)
        finally:
            asyncio.run(resolved.aclose())

    def test_missing_admin_key_leaves_key_empty(self):
        """Saknas båda nycklarna ska api_key vara tom — inte krascha."""
        self.ICP.set_param('bifrost.admin_api_key', '')
        resolved = self._resolve()
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved.api_key, '')


@tagged('post_install', '-at_install')
class TestGatewayRejectionMapping(TransactionCase):
    """fix-bifrost-entitlement-drift: gateway-avslag blir begripliga fel.

    Bakgrund: ett 403 combo_forbidden visades som "Anslutningen till
    AI-servern bröts". Sessionen dog med round_count: 0 och användaren fick
    ingen ledtråd. Mappningen skiljer ett AVSLAG (modellen är inte entitled /
    finns inte) från ett ANSLUTNINGSFEL (timeout, DNS).
    """

    def _map(self, status, body, model='OR+deepseek'):
        from odoo.addons.ai_agent_core.core.provider import (
            map_gateway_rejection,
        )
        return map_gateway_rejection(status, body, model)

    def test_real_combo_forbidden_maps(self):
        """Det verkliga gateway-svaret (reproducerat 2026-10-06)."""
        body = ('{"error":{"message":"combo is not entitled for this '
                'virtual key","type":"combo_forbidden"}}')
        rej = self._map(403, body)
        self.assertIsNotNone(rej)
        self.assertEqual(rej.model, 'OR+deepseek')
        self.assertEqual(rej.reason, 'combo_forbidden')
        self.assertEqual(rej.status_code, 403)

    def test_message_names_the_model(self):
        body = '{"error":{"type":"combo_forbidden"}}'
        rej = self._map(403, body, model='OR+deepseek')
        self.assertIn('OR+deepseek', str(rej))

    def test_model_not_found_maps(self):
        rej = self._map(404, '{"error":{"type":"model_not_found"}}', 'x')
        self.assertIsNotNone(rej)
        self.assertEqual(rej.reason, 'model_not_found')

    def test_unknown_format_falls_back(self):
        """Okänt format → None, så anroparen visar sitt generiska fel."""
        self.assertIsNone(self._map(500, '{"weird":true}'))
        self.assertIsNone(self._map(502, '<html>bad gateway</html>'))
        self.assertIsNone(self._map(403, ''))

    def test_free_text_403_with_entitlement(self):
        """403 med känt meddelande men okänt type-fält."""
        rej = self._map(403, '{"error":{"message":"not entitled"}}')
        self.assertIsNotNone(rej)
        self.assertEqual(rej.reason, 'combo_forbidden')

    def test_rejection_is_not_retryable(self):
        """Ett avslag ska inte retrias — det är inte ett transient fel."""
        rej = self._map(403, '{"error":{"type":"combo_forbidden"}}')
        self.assertFalse(rej.retryable)


@tagged('post_install', '-at_install')
class TestCatalogDrift(TransactionCase):
    """Driftkontroll: skiljer 'katalog otillgänglig' från 'drift'."""

    def setUp(self):
        super().setUp()
        self.provider = self.env['ai.provider'].create({
            'name': 'Bifrost Drift Test',
            'provider_type': 'bifrost',
            'base_url': 'http://127.0.0.1:1/v1',  # stängd port → otillgänglig
            'is_bifrost': True,
        })

    def test_unreachable_catalog_is_not_drift(self):
        """En nere gateway får inte larma falskt som drift."""
        drift = self.provider._catalog_drift()
        self.assertFalse(drift['checked'],
                         'otillhämtad katalog ska ge checked=False')
        self.assertEqual(drift['missing_in_catalog'], [])
        self.assertEqual(drift['missing_in_register'], [])

    def test_fetch_catalog_names_fail_open(self):
        names, ok = self.provider._fetch_catalog_names(timeout=1)
        self.assertFalse(ok)
        self.assertIsNone(names)

    def test_drift_check_runs_without_crashing(self):
        """Cron-metoden ska tåla att alla gateways är nere."""
        report = self.env['ai.provider']._run_drift_check()
        self.assertIsInstance(report, list)

    def test_is_gateway_detects_bifrost(self):
        self.assertTrue(self.provider._is_gateway())


@tagged('post_install', '-at_install')
class TestModelCatalogValidation(TransactionCase):
    """Validering av ai.model.api_name är icke-blockerande (D2)."""

    def setUp(self):
        super().setUp()
        self.provider = self.env['ai.provider'].create({
            'name': 'Bifrost Validate Test',
            'provider_type': 'bifrost',
            'base_url': 'http://127.0.0.1:1/v1',  # otillgänglig
            'is_bifrost': True,
        })

    def test_save_succeeds_when_gateway_unreachable(self):
        """Fail-open: otillgänglig gateway hindrar inte konfiguration."""
        model = self.env['ai.model'].create({
            'name': 'some-model',
            'api_name': 'some-model',
            'provider': self.provider.id,
        })
        self.assertTrue(model.exists(), 'posten ska sparas ändå')

    def test_write_succeeds_when_gateway_unreachable(self):
        model = self.env['ai.model'].create({
            'name': 'm1', 'api_name': 'm1', 'provider': self.provider.id})
        model.write({'api_name': 'm2'})
        self.assertEqual(model.api_name, 'm2')

    def test_non_gateway_provider_skips_validation(self):
        """En direktkopplad provider ska inte katalog-valideras."""
        direct = self.env['ai.provider'].create({
            'name': 'Direct', 'provider_type': 'openai',
            'base_url': 'https://api.openai.com/v1',
        })
        self.assertFalse(direct._is_gateway())
        model = self.env['ai.model'].create({
            'name': 'gpt-4o', 'api_name': 'gpt-4o', 'provider': direct.id})
        self.assertTrue(model.exists())
