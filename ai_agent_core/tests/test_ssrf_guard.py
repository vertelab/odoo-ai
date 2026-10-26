# -*- coding: utf-8 -*-
"""SSRF-skydd i fetch_url (atgardbara-verktygsfel-kompatibelt).

Verktyget ``fetch_url`` tar en URL från en LLM. LLM:en kan ha fått URL:en
från en webbsida (prompt injection), och hämtningen sker inifrån
Odoo-serverns nätverk. Utan skydd kan en sådan URL nå molnmetadata,
interna admin-gränssnitt eller Odoo självt på loopback.

Testerna verifierar att skyddet avvisar interna adresser, att publika
adresser släpps igenom, och att redirects valideras på nytt per hopp.
Inga riktiga nätanrop görs — DNS-uppslag mot publika namn kan förekomma
men kräver ingen internetåtkomst för de blockerade fallen (literal-IP och
scheman kontrolleras utan DNS).
"""
import asyncio
import unittest

from odoo.tests import tagged
from odoo.tests.common import TransactionCase

from odoo.addons.ai_agent_core.core.ssrf import (
    SsrfBlocked,
    validate_redirect,
    validate_url,
)
from odoo.addons.ai_agent_core.core.tools import _looks_relevant


@tagged('post_install', '-at_install')
class TestSsrfGuard(TransactionCase):
    """Enhetstester för ssrf-modulen."""

    def test_allows_public_urls(self):
        for url in (
            'https://docs.saltproject.io/',
            'http://example.com/page',
            'https://1.1.1.1/',
        ):
            with self.subTest(url=url):
                self.assertEqual(validate_url(url), url)

    def test_blocks_loopback(self):
        for url in ('http://127.0.0.1/', 'http://[::1]/',
                    'http://[::ffff:127.0.0.1]/'):
            with self.subTest(url=url):
                with self.assertRaises(SsrfBlocked):
                    validate_url(url)

    def test_blocks_cloud_metadata(self):
        """169.254.169.254 är AWS/GCP/Azure metadata — högsta prioritet."""
        with self.assertRaises(SsrfBlocked):
            validate_url('http://169.254.169.254/latest/meta-data/')

    def test_blocks_private_ranges(self):
        for url in ('http://192.168.11.22/', 'http://10.0.0.1/',
                    'http://172.16.0.1/', 'http://0.0.0.0/'):
            with self.subTest(url=url):
                with self.assertRaises(SsrfBlocked):
                    validate_url(url)

    def test_blocks_ipv6_private(self):
        for url in ('http://[fe80::1]/', 'http://[fd00::1]/'):
            with self.subTest(url=url):
                with self.assertRaises(SsrfBlocked):
                    validate_url(url)

    def test_blocks_non_http_schemes(self):
        """file: kan läsa lokal disk, data: kan bädda in godtyckligt."""
        for url in ('file:///etc/passwd', 'ftp://example.com/',
                    'gopher://example.com/', 'data:text/html,<h1>x</h1>'):
            with self.subTest(url=url):
                with self.assertRaises(SsrfBlocked):
                    validate_url(url)

    def test_blocks_empty_and_hostless(self):
        for url in ('', 'http://'):
            with self.subTest(url=url):
                with self.assertRaises(SsrfBlocked):
                    validate_url(url)

    def test_blocks_localhost_by_name(self):
        """DNS-rebindning: ett namn som pekar på loopback ska avvisas."""
        with self.assertRaises(SsrfBlocked):
            validate_url('http://localhost/')

    def test_redirect_hop_limit(self):
        with self.assertRaises(SsrfBlocked):
            validate_redirect('https://example.com/', 'https://example.com/x', 6)

    def test_redirect_to_private_blocked(self):
        with self.assertRaises(SsrfBlocked):
            validate_redirect('https://example.com/', 'http://127.0.0.1/', 1)

    def test_relative_redirect_allowed(self):
        """En relativ redirect ärver värden och är redan validerad."""
        self.assertEqual(
            validate_redirect('https://example.com/a', '/b', 1), '/b')


@tagged('post_install', '-at_install')
class TestFetchUrlSsrf(TransactionCase):
    """Verifierar att fetch_url-verktyget använder skyddet."""

    def _run(self, url):
        from odoo.addons.ai_agent_core.core.tools import _tool_fetch_url
        return asyncio.run(_tool_fetch_url(url=url))

    def test_fetch_url_blocks_metadata(self):
        import json
        raw = self._run('http://169.254.169.254/latest/meta-data/')
        data = json.loads(raw)
        self.assertEqual(data.get('parameter'), 'url')
        self.assertIn('Blocked', data.get('hint', ''))
        # Får inte vara omförsökbart med samma argument.
        self.assertFalse(data.get('retryable', True))

    def test_fetch_url_blocks_loopback(self):
        import json
        raw = self._run('http://127.0.0.1:8069/web/database/manager')
        data = json.loads(raw)
        self.assertEqual(data.get('parameter'), 'url')

    def test_fetch_url_blocks_file_scheme(self):
        """file: avvisas av schemakontrollen innan SSRF-kontrollen nås."""
        import json
        raw = self._run('file:///etc/passwd')
        data = json.loads(raw)
        self.assertEqual(data.get('parameter'), 'url')
        self.assertFalse(data.get('retryable', True))
        self.assertTrue(data.get('hint'))


class TestLookRelevant(unittest.TestCase):
    """Relevanskontrollen som fångar DDGS skräpsvar.

    DuckDuckGo svarar ibland med orelaterade träffar (Telenor för
    "saltstack pillar") i stället för att returnera 429. Kontrollen är
    grov med flit — den ska bara fånga uppenbart skräp.
    """

    def test_relevant(self):
        results = [{'title': 'Pillar Walkthrough - Salt',
                    'body': 'Pillar is important',
                    'href': 'https://docs.saltproject.io/pillar'}]
        self.assertTrue(_looks_relevant('saltstack pillar', results))

    def test_irrelevant(self):
        # Verkligt fall: "saltstack pillar" gav Telenor.
        results = [{'title': 'Telenor på svenskehandel',
                    'body': 'Finansavisen',
                    'href': 'https://finansavisen.no/telenor'}]
        self.assertFalse(_looks_relevant('saltstack pillar', results))

    def test_short_query_trusted(self):
        # Kan inte bedöma korta frågor — lita på resultatet.
        self.assertTrue(_looks_relevant('ab cd',
                                        [{'title': 'X', 'body': '', 'href': ''}]))

    def test_empty_results(self):
        self.assertTrue(_looks_relevant('saltstack pillar', []))
