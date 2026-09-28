# -*- coding: utf-8 -*-
"""Tester för åtgärdbara verktygsfel (atgardbara-verktygsfel).

Verifierar:
  (1.1–1.7) generiska verktyg ger strukturerade fel med parameter, förväntat
            format, exempel och retryable
  (2.1)     retryable sätts efter orsak (rättbart vs icke-rättbart)
  (2.2)     odoo_write pekar på rätt verktyg för HTML-fält
  (3.1–3.4) data-definierade verktyg ger samma felstruktur
  (4.1)     ingen verktygsväg returnerar fri text utan vägledning
  (4.2)     generiskt och data-definierat verktyg ger samma struktur
"""

import json

from odoo.tests import common, tagged

from odoo.addons.ai_agent_core.core.tools import (
    ToolError, _missing_argument_error, _invalid_value_error,
    _missing_record_error, _internal_error,
    _tool_describe_model, _tool_calculator,
)


@tagged('post_install', '-at_install')
class TestActionableToolErrors(common.TransactionCase):
    """Felstrukturen i generiska och data-definierade verktyg."""

    # ── 1.1–1.3 generiska verktyg ─────────────────────────────────────

    def test_web_search_empty_query_is_actionable(self):
        """1.1: tomt query ⇒ parameter, förväntat format, exempel."""
        from odoo.addons.ai_agent_core.core.tools import _tool_web_search
        import asyncio
        raw = asyncio.run(_tool_web_search(query=''))
        data = json.loads(raw)
        self.assertEqual(data['parameter'], 'query')
        self.assertTrue(data['expected'])
        self.assertIn('example', data['error'].lower())
        self.assertTrue(data['retryable'])
        self.assertEqual(data['tool_name'], 'web_search')

    def test_fetch_url_empty_is_actionable(self):
        """1.2: tomt url ⇒ förväntat format http(s) och exempel."""
        from odoo.addons.ai_agent_core.core.tools import _tool_fetch_url
        import asyncio
        data = json.loads(asyncio.run(_tool_fetch_url(url='')))
        self.assertEqual(data['parameter'], 'url')
        self.assertIn('http', data['expected'])
        self.assertTrue(data['retryable'])

    def test_fetch_url_bad_scheme_names_actual(self):
        """1.2: fel schema ⇒ faktiskt värde + förväntat format."""
        from odoo.addons.ai_agent_core.core.tools import _tool_fetch_url
        import asyncio
        data = json.loads(asyncio.run(_tool_fetch_url(url='ftp://x.se')))
        self.assertEqual(data['parameter'], 'url')
        self.assertEqual(data['actual'], 'ftp://x.se')
        self.assertIn('http', data['expected'])

    def test_calculator_names_disallowed_chars(self):
        """1.3: avvisat uttryck ⇒ namnger vilka tecken som är tillåtna."""
        import asyncio
        data = json.loads(asyncio.run(_tool_calculator('2 + abc')))
        self.assertEqual(data['parameter'], 'expression')
        self.assertIn('digits', data['expected'])
        self.assertTrue(data['actual'])

    def test_calculator_empty_expression(self):
        """1.3: tomt uttryck ⇒ saknat argument, inte 'disallowed'."""
        import asyncio
        data = json.loads(asyncio.run(_tool_calculator('')))
        self.assertEqual(data['parameter'], 'expression')
        self.assertIn('Missing required argument', data['error'])

    # ── 1.7 describe_model: tomt argument ─────────────────────────────

    def test_describe_model_empty_is_missing_not_unknown(self):
        """1.7: tomt argument ⇒ SAKNAT argument, inte 'Unknown model: '."""
        data = json.loads(_tool_describe_model(self.env, model=''))
        self.assertIn('Missing required argument', data['error'])
        self.assertEqual(data['parameter'], 'model')
        self.assertNotIn('Unknown model', data['error'])
        self.assertIn('res.partner', data['error'])

    def test_describe_model_unknown_is_not_retryable(self):
        """1.7/2.1: okänd modell ⇒ retryable=False."""
        data = json.loads(
            _tool_describe_model(self.env, model='finns.inte.modell'))
        self.assertEqual(data['parameter'], 'model')
        self.assertFalse(data['retryable'],
                         'okänd modell kan inte rättas med ett nytt försök')

    def test_describe_model_valid_still_works(self):
        """Ingen regression: en giltig modell beskrivs fortfarande."""
        raw = _tool_describe_model(self.env, model='res.partner')
        data = json.loads(raw)
        self.assertNotIn('error', data)

    # ── 2.1 retryable efter orsak ─────────────────────────────────────

    def test_retryable_true_for_correctable_errors(self):
        """2.1: saknat argument och fel format är omförsökbara."""
        self.assertTrue(
            _missing_argument_error('x', 'a value').retryable)
        self.assertTrue(
            _invalid_value_error('x', 'a number', 'abc').retryable)

    def test_retryable_false_for_uncorrectable_errors(self):
        """2.1: resurs finns inte och internt fel är inte omförsökbara."""
        self.assertFalse(
            _missing_record_error('ai.skill', 999).retryable)
        self.assertFalse(
            _internal_error('fetch_url', RuntimeError('nere')).retryable)

    def test_internal_error_states_it_is_internal(self):
        """Kategori C: internt fel säger att det inte är ett argumentfel."""
        err = _internal_error('fetch_url', RuntimeError('timeout'))
        self.assertIn('internal error', err.expected)
        self.assertFalse(err.retryable)

    # ── 2.2 odoo_write pekar på rätt verktyg ──────────────────────────

    def test_odoo_write_html_field_points_to_create(self):
        """2.2: HTML-fält ⇒ vägledningen namnger odoo_create."""
        from odoo.addons.ai_agent_core.core.tools import _tool_odoo_write
        # document.page.content är html och får inte skrivas via odoo_write.
        #
        # FYND 2026-09-23: `document_page` är inte ett beroende i manifestet
        # — i en installation utan modulen finns modellen inte i registret,
        # och `self.env['document.page']` kastar KeyError INNAN skipTest-
        # guarden nås. Kolla registret först.
        if 'document.page' not in self.env:
            self.skipTest('document.page saknas (document_page ej installerad)')
        page = self.env['document.page'].search([], limit=1)
        if not page:
            self.skipTest('document.page saknas')
        raw = _tool_odoo_write(
            self.env, model='document.page', ids=[page.id],
            values={'content': '<p>x</p>'})
        data = json.loads(raw)
        self.assertIn('error', data)
        self.assertIn('odoo_create', data['expected'],
                      'felet ska peka på rätt verktyg')

    # ── 3.1–3.4 data-definierade verktyg ──────────────────────────────

    def _youtube_tool(self, name):
        return self.env['ai.tool'].search([('name', '=', name)], limit=1)

    def test_youtube_tools_have_actionable_errors(self):
        """3.2–3.4: YouTube-verktygen ger samma felstruktur."""
        for tname in ('youtube_get_transcript', 'youtube_search',
                      'youtube_channel', 'youtube_playlist'):
            tool = self._youtube_tool(tname)
            if not tool:
                continue
            # Tomt anrop ska ge strukturerat fel (ingen API-nyckel behövs
            # eftersom argumentvalideringen kommer först).
            raw = tool._execute_tool({})
            data = json.loads(raw)
            self.assertIn('error', data, f'{tname}: inget fel returnerat')
            self.assertIn('parameter', data,
                          f'{tname}: felet saknar parameter')
            self.assertIn('retryable', data,
                          f'{tname}: felet saknar retryable')
            self.assertIn('tool_name', data,
                          f'{tname}: felet saknar tool_name')

    # ── 4.2 samma struktur oavsett verktygstyp ────────────────────────

    def test_same_structure_across_tool_types(self):
        """4.2: generiskt och data-definierat verktyg ger samma nycklar."""
        import asyncio
        from odoo.addons.ai_agent_core.core.tools import _tool_web_search
        generic = json.loads(asyncio.run(_tool_web_search(query='')))
        tool = self._youtube_tool('youtube_search')
        if not tool:
            self.skipTest('youtube_search saknas')
        data_tool = json.loads(tool._execute_tool({}))
        expected_keys = {'error', 'parameter', 'expected', 'actual',
                         'retryable', 'tool_name'}
        self.assertTrue(expected_keys <= set(generic),
                        f'generiskt verktyg saknar: '
                        f'{expected_keys - set(generic)}')
        self.assertTrue(expected_keys <= set(data_tool),
                        f'data-verktyg saknar: {expected_keys - set(data_tool)}')

    # ── ToolError-serilisering ────────────────────────────────────────

    def test_tool_error_text_and_json_agree(self):
        """Felet är läsbart för modellen och strukturerat för kedjan."""
        err = _missing_argument_error('query', 'a query', example='test')
        text = err.to_text()
        self.assertIn('query', text)
        self.assertIn('Retryable: yes', text)
        data = json.loads(err.to_json())
        self.assertEqual(data['parameter'], 'query')
        self.assertTrue(data['retryable'])
