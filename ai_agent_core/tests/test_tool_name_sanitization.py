# -*- coding: utf-8 -*-
"""Regressionstester för verktygsnamns sanitering (fix-tool-name-sanitization).

Felklass: **ett namn som byggs ur mänsklig text når providern utan att
valideras mot providerns format**. `_sanitize_tool_name` translittererade
bara icke-ASCII och släppte igenom ASCII-specialtecken.

Symptomet i drift (ledningssystem 2026-11-05): webbchatten svarade
"❌ Anslutningen till AI-servern bröts" för medarbetare med en specialist
vars namn innehöll `&`:

    [InvalidParameter] Invalid parameter: invalid function name format
    call_specialist_seo_&_geo_specialist

Felet såg ut som ett nätverksfel, men var ett 400 från providern.
"""

import re

from odoo.tests import common

from odoo.addons.ai_agent_core.core.tools import _sanitize_tool_name

#: Providerns format. Samma regex som saniteringen verkställer.
VALID_NAME = re.compile(r'^[a-zA-Z0-9_-]+$')


class TestToolNameSanitization(common.TransactionCase):
    """3.1–3.4: regex, idempotens, ordning, ingen regression."""

    def test_ampersand_is_replaced(self):
        """3.1: `&` får inte nå providern — det gav 400 i drift.

        Fallerar på koden före rättningen: den returnerade namnet oförändrat
        eftersom `&` är ASCII.
        """
        result = _sanitize_tool_name('seo_&_geo_specialist')
        self.assertTrue(
            VALID_NAME.match(result),
            'saniterat namn %r matchar inte ^[a-zA-Z0-9_-]+$' % result)
        self.assertNotIn('&', result)

    def test_arrow_is_replaced(self):
        """`→` (U+2192) bryts inte ner av NFKD — måste ersättas."""
        result = _sanitize_tool_name('mail_→_partner')
        self.assertTrue(
            VALID_NAME.match(result),
            'saniterat namn %r matchar inte regexet' % result)
        self.assertNotIn('→', result)

    def test_specialist_tool_name_is_valid(self):
        """Ett specialist-verktyg byggt ur ett agentnamn med `&` blir giltigt."""
        # Samma konstruktion som specialist_tools():
        name = 'SEO & GEO-specialist'
        safe = _sanitize_tool_name(name.strip().lower().replace(' ', '_'))
        tool_name = f'call_specialist_{safe}'
        self.assertTrue(
            VALID_NAME.match(tool_name),
            'specialist-verktyget %r matchar inte regexet' % tool_name)

    def test_sanitization_is_idempotent(self):
        """3.2: f(f(x)) == f(x) — krävs av uppslaget i stream.py."""
        cases = [
            'seo_&_geo_specialist', 'mail_→_partner', 'allmän_kärna',
            'a & b', 'ok_name', '', 'SEO & GEO-specialist',
            'Enterprise Architect — Projekt', 'odoo_search',
        ]
        for raw in cases:
            once = _sanitize_tool_name(raw)
            twice = _sanitize_tool_name(once)
            self.assertEqual(
                once, twice,
                'saniteringen är inte idempotent för %r: %r != %r'
                % (raw, once, twice))

    def test_transliteration_happens_before_replacement(self):
        """3.3: `å` ska bli `a`, inte `_`."""
        self.assertEqual(
            _sanitize_tool_name('allmän & kärna'), 'allman_karna',
            'translittereringen måste ske före ersättningen — annars blir '
            'det allm_n_k_rna')
        self.assertEqual(
            _sanitize_tool_name('allmän_kärna'), 'allman_karna')

    def test_valid_names_pass_through(self):
        """3.4: ingen regression för redan giltiga namn."""
        for good in ('odoo_search', 'call_specialist_research',
                     'driftlarm_update_assessment', 'bg_wait', 'a_b-c'):
            self.assertEqual(
                _sanitize_tool_name(good), good,
                'giltigt namn %r ändrades till %r'
                % (good, _sanitize_tool_name(good)))

    def test_collapsed_repeats_and_trimmed_edges(self):
        """Upprepade `_` kollapsas och kanter trimmas.

        Utan kollaps blir `a & b` → `a___b`, vilket är giltigt men oläsligt
        och känsligt för hur många mellanslag någon råkade skriva.
        """
        self.assertEqual(_sanitize_tool_name('a & b'), 'a_b')
        self.assertEqual(_sanitize_tool_name('  x  '), 'x')
        self.assertEqual(_sanitize_tool_name('&&&'), '')
