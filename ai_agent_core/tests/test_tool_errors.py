# -*- coding: utf-8 -*-
"""Tester för verktygsfel och åtgärdbar felrapportering
(improve-ai-coworker-memory-and-tools grupp 2).

Verifierar att:
  (2.1) ToolError bär parameter/förväntat/faktiskt/kan-göras-om och
        serialiseras till både LLM-läsbar text och JSON,
  (2.2) felaktig parameter ger ett åtgärdbart fel (parameternamn +
        förväntat format),
  (2.3) okänt fält namnges och giltiga fält förtecknas.
"""

import json

from odoo.tests import common, tagged

from odoo.addons.ai_agent_core.core.tools import (
    ToolError, _tool_odoo_create, _tool_odoo_write, _tool_odoo_search,
)


@tagged('post_install')
class TestToolErrors(common.TransactionCase):
    """Strukturerade, åtgärdbara verktygsfel."""

    # ── 2.1 Feltypen ───────────────────────────────────────────────────

    def test_tool_error_serializes_to_text(self):
        """LLM-läsbar text innehåller parameter, förväntat och retryable."""
        err = ToolError(
            'Unknown field', parameter='foo', expected='one of: a, b',
            actual='foo', valid_fields=['a', 'b'], retryable=True,
            tool_name='odoo_create')
        text = err.to_text()
        self.assertIn('Parameter: foo', text)
        self.assertIn('Expected: one of: a, b', text)
        self.assertIn('Actual: foo', text)
        self.assertIn('Retryable: yes', text)

    def test_tool_error_serializes_to_json(self):
        """Maskinläsbar struktur för kvalitetsloopen."""
        err = ToolError('msg', parameter='p', expected='e', actual='a',
                        valid_fields=['x'], retryable=False,
                        tool_name='t')
        data = json.loads(err.to_json())
        self.assertEqual(data['error'], 'msg')
        self.assertEqual(data['parameter'], 'p')
        self.assertEqual(data['expected'], 'e')
        self.assertEqual(data['actual'], 'a')
        self.assertEqual(data['valid_fields'], ['x'])
        self.assertFalse(data['retryable'])
        self.assertEqual(data['tool_name'], 't')

    # ── 2.2 Felaktig parameter ─────────────────────────────────────────

    def test_create_with_bad_field_is_actionable(self):
        """Felaktigt fält vid create → parameternamn + giltiga fält."""
        raw = _tool_odoo_create(
            self.env, model='res.partner',
            values={'name': 'X', 'no_such_field': 'y'})
        data = json.loads(raw)
        self.assertIn('no_such_field', data['error'])
        self.assertEqual(data['parameter'], 'no_such_field')
        self.assertIn('one of:', data['expected'])
        self.assertTrue(data['valid_fields'])
        self.assertIn('name', data['valid_fields'])

    def test_write_with_readonly_field_is_actionable(self):
        """Icke-skrivbart fält vid write → åtgärdbart fel."""
        partner = self.env['res.partner'].create({'name': 'Write Test'})
        raw = _tool_odoo_write(
            self.env, model='res.partner', ids=[partner.id],
            values={'display_name': 'borde nekas'})
        data = json.loads(raw)
        self.assertIn('not writable', data['error'])
        self.assertTrue(data['parameter'])
        self.assertIn('odoo_call_method', data['expected'])

    # ── 2.3 Okänt fält ─────────────────────────────────────────────────

    def test_search_with_unknown_field_names_it(self):
        """Okänt fält vid search → fältet namnges + giltiga fält listas."""
        raw = _tool_odoo_search(
            self.env, model='res.partner',
            domain=[], fields=['id', 'totally_bogus_field'])
        data = json.loads(raw)
        self.assertIn('totally_bogus_field', data['error'])
        self.assertEqual(data['parameter'], 'totally_bogus_field')
        self.assertTrue(data['valid_fields'])
        self.assertIn('name', data['valid_fields'])

    def test_valid_create_still_works(self):
        """Giltigt anrop påverkas inte av felvägen."""
        raw = _tool_odoo_create(
            self.env, model='res.partner', values={'name': 'OK Partner'})
        data = json.loads(raw)
        self.assertTrue(data.get('ok'))
        self.assertTrue(data.get('id'))
