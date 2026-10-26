# -*- coding: utf-8 -*-
"""Extrahering av läsbar text ur HTML.

Ersätter ``soup.get_text()``, som gav menyrader, cookie-banners och
brödsmulor i stället för artikeltext. Testerna använder statisk HTML —
inga nätanrop — så de är snabba och deterministiska.
"""
from odoo.tests import tagged
from odoo.tests.common import TransactionCase

from odoo.addons.ai_agent_core.core.extract import (
    extract_meta_refresh,
    extract_readable,
)


@tagged('post_install', '-at_install')
class TestExtractReadable(TransactionCase):

    def test_prefers_article_over_navigation(self):
        html = """
        <html><body>
          <nav><a href="/a">Hem</a><a href="/b">Produkter</a>
               <a href="/c">Kontakt</a><a href="/d">Om oss</a></nav>
          <article>
            <h1>Rubrik</h1>
            <p>Detta är artikelns första stycke med tillräckligt mycket
               text för att räknas som innehåll i testet.</p>
            <p>Andra stycket fortsätter beskriva ämnet utförligt och
               innehåller också en ansenlig mängd löpande text.</p>
          </article>
          <footer>Copyright 2026</footer>
        </body></html>
        """
        text = extract_readable(html, 'https://example.com/')
        self.assertIn('artikelns första stycke', text)
        self.assertNotIn('Copyright', text)
        self.assertNotIn('Produkter', text)

    def test_strips_script_and_style(self):
        html = """
        <html><body>
          <script>var x = 'hemlig';</script>
          <style>.a { color: red; }</style>
          <article><p>Riktigt innehåll som ska finnas kvar i resultatet
             och vara tillräckligt långt för att passera tröskeln.</p></article>
        </body></html>
        """
        text = extract_readable(html, 'https://example.com/')
        self.assertIn('Riktigt innehåll', text)
        self.assertNotIn('hemlig', text)
        self.assertNotIn('color: red', text)

    def test_strips_cookie_banner_by_class(self):
        html = """
        <html><body>
          <div class="cookie-consent-banner">
            Vi använder kakor. Godkänn eller avböj.
          </div>
          <article><p>Artikeln handlar om något helt annat och har
             tillräckligt med text för att bli vald som innehåll.</p></article>
        </body></html>
        """
        text = extract_readable(html, 'https://example.com/')
        self.assertIn('Artikeln handlar', text)
        self.assertNotIn('kakor', text)

    def test_respects_max_length(self):
        html = '<article><p>' + ('ord ' * 500) + '</p></article>'
        text = extract_readable(html, 'https://example.com/', max_length=100)
        self.assertLessEqual(len(text), 100)

    def test_empty_html_returns_empty(self):
        self.assertEqual(extract_readable('', 'https://example.com/'), '')

    def test_meta_refresh_absolute(self):
        html = '<meta http-equiv="refresh" content="0; url=https://x.test/a">'
        self.assertEqual(
            extract_meta_refresh(html, 'https://example.com/'),
            'https://x.test/a')

    def test_meta_refresh_relative_resolved(self):
        html = '<meta http-equiv="refresh" content="0; url=en/latest/contents.html" />'
        self.assertEqual(
            extract_meta_refresh(html, 'https://docs.example.com/'),
            'https://docs.example.com/en/latest/contents.html')

    def test_meta_refresh_absent(self):
        self.assertIsNone(
            extract_meta_refresh('<html><body>hej</body></html>',
                                 'https://example.com/'))
