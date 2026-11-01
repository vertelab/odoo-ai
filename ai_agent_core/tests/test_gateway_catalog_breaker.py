# -*- coding: utf-8 -*-
"""Tester för katalogcache och circuit breaker
(gateway-anropseffektivitet D2, D3, D4).

Verifierar att:
  (a) kataloghämtning cachas per provider i körkontexten — två anrop gör
      en hämtning,
  (b) en tom men hämtad katalog cachas som "hämtad och tom",
  (c) ett gateway-avslag (402/403/404) kopplar bort providern,
  (d) en bortkopplad provider inte probas igen under perioden,
  (e) breaker-perioden löper ut och providern tas i bruk igen,
  (f) avslagstyperna hålls åtskilda i loggningen,
  (g) probe-timeouten är kort och skild från inferens-timeouten.

Testerna rör inte nätverket: `_fetch_catalog_names` och
`_fetch_config_map` patchas så att inga riktiga gateway-anrop görs.
"""

from unittest import mock
import urllib.error

from odoo.tests import common


class TestCatalogCacheAndBreaker(common.TransactionCase):
    """Katalogcache per provider + breaker på gateway-avslag."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Provider = cls.env['ai.provider']
        cls.provider = cls.Provider.create({
            'name': 'GW Test Provider',
            'provider_type': 'bifrost',
            'base_url': 'http://127.0.0.1:9/v1',
            'api_key': 'test-key',
        })

    def setUp(self):
        super().setUp()
        # Nollställ breakern mellan tester (modulglobal tabell).
        from odoo.addons.ai_agent_core.models import ai_provider
        ai_provider._BREAKER_TABLE.pop(self.provider.id, None)
        ai_provider._CATALOG_CACHE.pop(self.provider.id, None)

    # -- Katalogcache (D2) --

    def test_catalog_cached_per_provider(self):
        """(a) Två anrop gör en hämtning.

        Vi patchar HTTP-lagret (`urllib.request.urlopen`), inte
        `_fetch_catalog_names` — annars kringgås cache-läsningen som
        ligger inuti metoden och testet mäter fel sak.
        """
        calls = []

        class FakeResp:
            def __init__(self, body):
                self._body = body.encode()
            def read(self):
                return self._body
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False

        def fake_urlopen(req, timeout=None, context=None):
            calls.append(timeout)
            return FakeResp('{"data": [{"id": "model-a"}, '
                            '{"id": "model-b"}]}')

        with mock.patch('urllib.request.urlopen', side_effect=fake_urlopen):
            first, ok1 = self.provider._fetch_catalog_names()
            second, ok2 = self.provider._fetch_catalog_names()
        self.assertTrue(ok1 and ok2)
        self.assertEqual(first, second)
        self.assertEqual(len(calls), 1,
                         'Andra anropet ska läsa cachen, inte hämta igen')

    def test_empty_catalog_is_cached(self):
        """(b) Tom men hämtad katalog cachas som hämtad."""
        calls = []

        class FakeResp:
            def __init__(self, body):
                self._body = body.encode()
            def read(self):
                return self._body
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False

        def fake_urlopen(req, timeout=None, context=None):
            calls.append(1)
            return FakeResp('{"data": []}')

        with mock.patch('urllib.request.urlopen', side_effect=fake_urlopen):
            names, ok = self.provider._fetch_catalog_names()
            self.assertEqual(names, [])
            self.assertTrue(ok, 'Tom katalog är hämtad, inte misslyckad')
            self.provider._fetch_catalog_names()
        self.assertEqual(len(calls), 1)

    # -- Circuit breaker (D3) --

    def test_rejection_trips_breaker(self):
        """(c) 402 kopplar bort providern."""
        err = urllib.error.HTTPError(
            'http://x/v1/models', 402, 'Payment Required', {}, None)
        with mock.patch('urllib.request.urlopen', side_effect=err):
            names, ok = self.provider._fetch_catalog_names()
        self.assertFalse(ok)
        self.assertTrue(self.provider._breaker_active(),
                        'Providern ska vara bortkopplad efter 402')

    def test_breaker_blocks_further_probes(self):
        """(d) Bortkopplad provider probas inte igen."""
        self.provider._breaker_trip(402, 'credits are depleted')
        with mock.patch('urllib.request.urlopen') as m:
            names, ok = self.provider._fetch_catalog_names()
            m.assert_not_called()
        self.assertFalse(ok)

    def test_breaker_expires(self):
        """(e) Perioden löper ut och providern släpps igen."""
        from datetime import timedelta
        from odoo import fields as odoo_fields
        from odoo.addons.ai_agent_core.models import ai_provider
        # Sätt en tidpunkt i det förflutna.
        ai_provider._BREAKER_TABLE[self.provider.id] = (
            odoo_fields.Datetime.now() - timedelta(seconds=1))
        self.assertFalse(self.provider._breaker_active(),
                         'Utgången period ska inte blockera')

    def test_rejection_reason_distinguishes_types(self):
        """(f) Avslagstyper hålls åtskilda — inkl. provider/error_type.

        Gatewayens svar (v2.3.0) bär `extra_fields.provider` och
        `extra_fields.error_type`; de två senare gör att en obehörig
        modell (403 `policy_model_blocked`) går att skilja från ett
        generiskt `combo_forbidden` i Odoo-loggen (D5).
        """
        import io

        def make(code, body):
            return urllib.error.HTTPError(
                'http://x', code, 'err', {}, io.BytesIO(body.encode()))

        credit = make(402, '{"error": {"type": "credits_are_depleted"}}')
        forbidden = make(403, '{"error": {"type": "combo_forbidden"}}')
        missing = make(404, '{"error": {"type": "model_not_found"}}')
        self.assertEqual(self.Provider._rejection_reason(credit),
                         'credits_are_depleted')
        self.assertEqual(self.Provider._rejection_reason(forbidden),
                         'combo_forbidden')
        self.assertEqual(self.Provider._rejection_reason(missing),
                         'model_not_found')

        # Riktigt gateway-svar: 403 med extra_fields (provider + error_type).
        blocked = make(403, (
            '{"type": "model_blocked", '
            '"error": {"message": "Model is not allowed"}, '
            '"extra_fields": {"provider": "cerebras", '
            '"original_model_requested": "gemma-4-31b", '
            '"error_type": "policy_model_blocked"}}'))
        reason = self.Provider._rejection_reason(blocked)
        self.assertIn('model_blocked', reason)
        self.assertIn('provider=cerebras', reason)
        self.assertIn('error_type=policy_model_blocked', reason)

        # 429: provider i extra_fields, error_type saknas.
        limited = make(429, (
            '{"type": "rate_limited", "code": "1300", '
            '"error": {"type": "rate_limited", "code": "1300", '
            '"message": "Rate limit exceeded"}, '
            '"extra_fields": {"provider": "mistral", "latency": 85}}'))
        reason = self.Provider._rejection_reason(limited)
        self.assertIn('rate_limited', reason)
        self.assertIn('provider=mistral', reason)
        self.assertNotIn('error_type=', reason)

    def test_non_rejection_does_not_trip(self):
        """Ett nätverksfel (inte avslag) kopplar inte bort providern."""
        with mock.patch('urllib.request.urlopen',
                        side_effect=OSError('connection refused')):
            names, ok = self.provider._fetch_catalog_names()
        self.assertFalse(ok)
        self.assertFalse(self.provider._breaker_active(),
                         'Nätverksfel är inte ett gateway-avslag')

    # -- Probe-timeout (D4) --

    def test_probe_timeout_is_short(self):
        """(g) Probe-timeouten är kort och skild från inferens."""
        self.assertLessEqual(self.provider._probe_timeout(), 10,
                             'Probe-timeout ska vara kort')
        self.assertLess(self.provider._probe_timeout(),
                        self.provider.timeout,
                        'Probe-timeout ska skilja sig från inferens-timeout')
