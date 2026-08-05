# -*- coding: utf-8 -*-
"""Unit tests for ai_agent_core PWA (ai-chat-pwa change).

- Branding-upplösning (pwa_app_name / pwa_icon_bytes)
- Endpoints: /ai/manifest.webmanifest, /ai/sw.js, /ai/icon-*.png, /ai/install
"""

import base64
import struct

from odoo.tests import TransactionCase, HttpCase, tagged


def _png_size(data):
    """Return (width, height) from PNG bytes via IHDR (offset 16–23)."""
    if data[:8] != b'\x89PNG\r\n\x1a\n':
        return None
    w, h = struct.unpack('>II', data[16:24])
    return (w, h)


class TestPwaBranding(TransactionCase):
    """res.company.pwa_app_name / pwa_icon_bytes resolution (task 2.4)."""

    def setUp(self):
        super().setUp()
        self.Company = self.env['res.company']
        self.icp = self.env['ir.config_parameter'].sudo()
        self.company = self.Company.create({'name': 'ACME AB'})

    def tearDown(self):
        self.icp.set_param('ai_agent_core.pwa_name', False)
        self.icp.set_param('web.web_app_name', False)
        super().tearDown()

    def test_default_chat_name(self):
        """Default chat-namn = "AI <företagsnamn>"."""
        self.assertEqual(self.company.pwa_app_name('chat'), 'AI ACME AB')

    def test_default_backend_name(self):
        """Default backend-namn = företagsnamnet."""
        self.assertEqual(self.company.pwa_app_name('backend'), 'ACME AB')

    def test_configured_name_overrides(self):
        """Konfigurerat pwa_name gäller för båda apparna."""
        self.icp.set_param('ai_agent_core.pwa_name', 'ACME AI')
        self.assertEqual(self.company.pwa_app_name('chat'), 'ACME AI')
        self.assertEqual(self.company.pwa_app_name('backend'), 'ACME AI')

    def test_backend_respects_web_app_name(self):
        """web.web_app_name används för backend om den satts explicit."""
        self.icp.set_param('web.web_app_name', 'ACME Portal')
        self.assertEqual(self.company.pwa_app_name('backend'), 'ACME Portal')
        # Chatten påverkas inte av web.web_app_name
        self.assertEqual(self.company.pwa_app_name('chat'), 'AI ACME AB')

    def test_icon_bytes_valid_png(self):
        """pwa_icon_bytes returnerar giltig PNG (logga eller icon.png-fallback)."""
        data = self.company.pwa_icon_bytes()
        self.assertTrue(data, "pwa_icon_bytes ska returnera bytes")
        self.assertEqual(data[:8], b'\x89PNG\r\n\x1a\n', "ska vara PNG-signatur")

    def test_configured_icon_wins(self):
        """Konfigurerad pwa_icon vinner över loggan."""
        png = base64.b64decode(
            'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg==')
        self.company.pwa_icon = base64.b64encode(png)
        self.assertEqual(self.company.pwa_icon_bytes(), png)


@tagged('post_install', '-at_install')
class TestPwaEndpoints(HttpCase):
    """HTTP-endpoints för PWA (task 7.1)."""

    def test_sw_content_type(self):
        resp = self.url_open('/ai/sw.js')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers.get('Content-Type'), 'text/javascript')
        self.assertIn('fetch', resp.text)

    def test_ai_manifest_fields(self):
        resp = self.url_open('/ai/manifest.webmanifest')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers.get('Content-Type'), 'application/manifest+json')
        manifest = resp.json()
        self.assertEqual(manifest['start_url'], '/ai/chat')
        self.assertEqual(manifest['scope'], '/ai/')
        self.assertEqual(manifest['display'], 'standalone')
        sizes = {i['sizes'] for i in manifest['icons']}
        self.assertIn('192x192', sizes)
        self.assertIn('512x512', sizes)

    def test_backend_manifest_branded(self):
        resp = self.url_open('/web/manifest.webmanifest')
        self.assertEqual(resp.status_code, 200)
        manifest = resp.json()
        self.assertEqual(manifest['scope'], '/odoo')
        self.assertEqual(manifest['start_url'], '/odoo')
        # Ikonerna pekar på kundens ikon-endpoints
        self.assertTrue(any(i['src'].startswith('/ai/icon-') for i in manifest['icons']))

    def test_icon_sizes(self):
        for size in (192, 512, 180):
            resp = self.url_open(f'/ai/icon-{size}.png')
            self.assertEqual(resp.status_code, 200, f'icon-{size}')
            self.assertEqual(resp.headers.get('Content-Type'), 'image/png')
            dims = _png_size(resp.content)
            self.assertEqual(dims, (size, size), f'icon-{size} ska vara {size}x{size}')

    def test_install_hub(self):
        resp = self.url_open('/ai/install')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('Installera appar', resp.text)
        resp_ai = self.url_open('/ai/install?app=ai')
        self.assertEqual(resp_ai.status_code, 200)
        self.assertIn('/ai/manifest.webmanifest', resp_ai.text)
        resp_odoo = self.url_open('/ai/install?app=odoo')
        self.assertEqual(resp_odoo.status_code, 200)
        self.assertIn('/web/manifest.webmanifest', resp_odoo.text)
