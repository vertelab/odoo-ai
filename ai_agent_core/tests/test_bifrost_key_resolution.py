"""Regressionstester för nyckeluppslaget i provider-resolutionen.

Bakgrund (drift 2026-09-15): `resolve_provider_from_model()` läste
`provider.api_key` rakt av. En provider vars nyckel fanns i odoo.conf
(`ai_provider_api_key`) skickade ingen nyckel alls, och gatewayen svarade:

    403 Forbidden  {"error":{"message":"combo is not entitled for this
                    virtual key","type":"combo_forbidden"}}

Felet SER UT som en rättighetsfråga ("combo är inte tillåten för denna nyckel")
men är i själva verket en tom sträng.

Uppslaget är numera GENERISKT (provider-key-fallback): record → odoo.conf →
fel. Ingen leverantör nämns, och `bifrost.admin_api_key` ingår inte — den
används av /admin/*-ytan och får aldrig bli en providers anropsnyckel.

Lardom: när ett felmeddelande namnger en ORSAK (entitlement), kontrollera att
den underliggande datan (nyckeln) ens finns. "Combo är inte tillåten" och
"ingen nyckel skickades" ger samma HTTP-status men kräver olika åtgärd.
"""
from odoo.tests import tagged
from ._config_param_guard import ConfigParamGuardedCase
from odoo.tests.common import TransactionCase


@tagged('post_install', '-at_install')
class TestProviderKeyResolution(TransactionCase):
    """Nyckeluppslaget är generiskt: record → odoo.conf → fel.

    HISTORIK: den här klassen hette TestBifrostKeyResolution och pinnade en
    Bifrost-specifik fallback till `ir.config_parameter
    'bifrost.admin_api_key'`. Den fallbacken är borttagen — uppslaget ska
    inte veta vilken leverantör som anropar. Admin-nyckeln används av
    /admin/*-ytan och får aldrig bli en providers anropsnyckel.

    Det ursprungliga felet (drift 2026-09-15) var att `api_key` lästes rakt
    av, så en provider vars nyckel fanns i odoo.conf skickade ingen nyckel
    alls och gatewayen svarade 403 "combo is not entitled for this virtual
    key": ett fel som ser ut som en rättighetsfråga men är en tom sträng.
    """

    def setUp(self):
        super().setUp()
        from odoo.tools import config as odoo_config
        self._odoo_config = odoo_config
        self._saved = {k: odoo_config.get(k) for k in ('ai_provider_api_key',)}
        self.config_key = 'sk-bf-config-0000-1111-2222-333344445555'

        self.provider = self.env['ai.provider'].create({
            'name': 'Generic Provider',
            'provider_type': 'custom',
            'base_url': 'http://127.0.0.1:8081/v1',
            'api_key': '',          # TOMT — det är hela poängen
            'is_key_required': True,
        })
        self.model = self.env['ai.model'].create({
            'name': 'test-combo',
            'api_name': 'test-combo',
            'provider': self.provider.id,
        })

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                self._odoo_config.pop(k, None)
            else:
                self._odoo_config[k] = v
        super().tearDown()

    def _resolve(self):
        from odoo.addons.ai_agent_core.core.provider import (
            resolve_provider_from_model,
        )
        return resolve_provider_from_model(self.model)

    def test_empty_record_key_falls_back_to_config(self):
        """Tomt api_key → nyckeln ur odoo.conf används på anropsvägen."""
        self._odoo_config['ai_provider_api_key'] = self.config_key
        resolved = self._resolve()
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved.api_key, self.config_key)

    def test_own_key_wins_over_config(self):
        """Egen nyckel på providern ska vinna över odoo.conf."""
        self._odoo_config['ai_provider_api_key'] = self.config_key
        own = 'sk-bf-vk-egen-nyckel-aaaa-bbbb-cccc-ddddeeeeffff'
        self.provider.api_key = own
        resolved = self._resolve()
        self.assertEqual(resolved.api_key, own)

    def test_missing_key_is_flagged_then_raised_at_call(self):
        """Ingen nyckel → flaggan sätts vid uppslaget, felet kastas vid anropet.

        Uppslaget är en uppslagsfunktion och får inte kasta: recordet ska
        gå att lösa upp även när nyckeln saknas (verktyg, tester, UI). Men
        ANROPET ska ge ett begripligt fel i stället för att gå ut nyckellöst
        och få 403 "combo is not entitled" (FYND 2026-10-02).
        """
        import asyncio
        from odoo.addons.ai_agent_core.core.provider import ProviderError
        self._odoo_config['ai_provider_api_key'] = ''
        resolved = self._resolve()
        self.assertIsNotNone(resolved, 'uppslaget ska inte kasta')
        self.assertTrue(resolved.key_missing)
        with self.assertRaises(ProviderError) as ctx:
            asyncio.run(resolved.chat(model='m', messages=[]))
        msg = str(ctx.exception)
        self.assertIn('ai_provider_api_key', msg)
        self.assertIn('odoo.conf', msg)

    def test_missing_key_raises_in_chat_stream(self):
        """Även streaming-vägen ska ge felet, inte ett nyckellöst anrop."""
        import asyncio
        from odoo.addons.ai_agent_core.core.provider import ProviderError
        self._odoo_config['ai_provider_api_key'] = ''
        resolved = self._resolve()

        async def consume():
            async for _event in resolved.chat_stream(model='m', messages=[]):
                pass

        with self.assertRaises(ProviderError):
            asyncio.run(consume())

    def test_keyless_provider_is_not_flagged(self):
        """is_key_required=False → ingen flagga, inget fel (t.ex. lokal Ollama)."""
        self._odoo_config['ai_provider_api_key'] = ''
        self.provider.is_key_required = False
        resolved = self._resolve()
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved.api_key, '')
        self.assertFalse(resolved.key_missing)

    def test_keyless_provider_does_not_get_the_config_key(self):
        """Flaggan av → conf-nyckeln används INTE på anropsvägen heller.

        Regressionen: utan gaten fick varje nyckellös provider
        `ai_provider_api_key` på köpet — en lokal Ollama hade skickat
        Bifrosts VK till localhost:11434.
        """
        self._odoo_config['ai_provider_api_key'] = self.config_key
        self.provider.is_key_required = False
        resolved = self._resolve()
        self.assertEqual(resolved.api_key, '',
                         'conf-nyckeln läckte till en provider utan nyckelkrav')
        self.assertFalse(resolved.key_missing)

    def test_bifrost_header_is_x_virtual_key(self):
        """Bifrost-nyckeln ska hamna i X-Virtual-Key, inte Authorization."""
        import asyncio
        self._odoo_config['ai_provider_api_key'] = self.config_key
        self.provider.is_bifrost = True
        self.provider.provider_type = 'bifrost'
        resolved = self._resolve()
        client = asyncio.run(resolved._get_client())
        try:
            self.assertEqual(client.headers.get('X-Virtual-Key'),
                             self.config_key)
            self.assertNotIn('Authorization', client.headers)
        finally:
            asyncio.run(resolved.aclose())

    def test_admin_key_is_not_a_fallback(self):
        """bifrost.admin_api_key får ALDRIG bli en providers anropsnyckel.

        Uppslaget är generiskt — det nämner ingen leverantör. Admin-nyckeln
        används av /admin/*-ytan och ska inte kunna tränga in som en
        providers anropsnyckel via en bakväg.
        """
        self._odoo_config['ai_provider_api_key'] = ''
        self.env['ir.config_parameter'].sudo().set_param(
            'bifrost.admin_api_key', 'sk-bf-admin-only')
        self.provider.is_bifrost = True
        resolved = self._resolve()
        self.assertEqual(resolved.api_key, '',
                         'admin-nyckeln läckte in i anropsnyckeln')
        self.assertTrue(resolved.key_missing)


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
