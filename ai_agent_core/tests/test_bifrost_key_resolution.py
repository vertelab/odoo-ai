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
