# -*- coding: utf-8 -*-
"""Verktygsanropens kvalitet (tool-call-quality).

VARFÖR: session 28097 svarade "(inget svar)" på varje fråga. Rotorsaken var
historik-parningen (fixad tidigare), men analysen avslöjade tre svagheter som
överlevde den:

  1. Modellen skickade TOMMA argument — fyra anrop gick ut som `{}`. Felen
     som kom tillbaka var korrekta men pekade inte på nästa steg, så modellen
     upprepade mönstret.
  2. Ingen ingång till "vad finns" — fem `ir.model`-sökningar för att hitta
     mail-modellen.
  3. `interest_score` mäter intresse, inte vikt — nyhetsbreven rankades högst.

Dessa tester bevisar de tre fixarna.
"""

import json

from odoo.tests import common, tagged


@tagged('post_install', '-at_install', 'tool_quality')
class TestToolArgumentGate(common.TransactionCase):
    """Task 2: tomma argument når aldrig implementationen."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from odoo.addons.ai_agent_core.core.tools import Tool, builtin_tools
        cls.Tool = Tool
        cls.tools = {t.name: t for t in builtin_tools()}
        from odoo.addons.ai_agent_core.core.loop import AgentLoop
        cls.Loop = AgentLoop

    def _loop(self):
        """En loop utan provider — bara valideringen ska prövas."""
        from odoo.addons.ai_agent_core.core.tools import ToolRegistry
        return self.Loop(provider=None, tools=ToolRegistry())

    def _validate(self, tool_name, args):
        loop = self._loop()
        return loop._validate_required_args(self.tools[tool_name], args)

    def test_empty_dict_is_rejected(self):
        """`{}` för ett obligatoriskt argument avvisas."""
        out = self._validate('odoo_search', {})
        self.assertTrue(out, 'tomt anrop måste avvisas')
        data = json.loads(out)
        self.assertEqual(data['parameter'], 'model')
        self.assertTrue(data['retryable'])

    def test_blank_string_is_rejected(self):
        out = self._validate('okf_search', {'query': '   '})
        self.assertTrue(out)
        self.assertEqual(json.loads(out)['parameter'], 'query')

    def test_none_is_rejected(self):
        out = self._validate('okf_search', {'query': None})
        self.assertTrue(out)

    def test_empty_list_is_rejected(self):
        """En tom lista för ett obligatoriskt fält är också tomt."""
        out = self._validate('odoo_unlink', {'model': 'res.partner',
                                             'ids': []})
        self.assertTrue(out)
        self.assertEqual(json.loads(out)['parameter'], 'ids')

    def test_valid_args_pass(self):
        self.assertEqual(
            self._validate('okf_search', {'query': 'mail från Skanska'}), '')

    def test_optional_field_may_be_empty(self):
        """`period` är frivilligt — tom sträng ska INTE avvisas."""
        self.assertEqual(
            self._validate('okf_search',
                           {'query': 'mail', 'period': ''}), '')

    def test_error_carries_next_step(self):
        """Felet ska säga vad anroparen gör härnäst (D3)."""
        data = json.loads(self._validate('describe_model', {}))
        self.assertIn('next_step', data)
        self.assertIn('describe_available', data['next_step'])

    def test_gate_can_be_disabled(self):
        """Systemparametern stänger av grinden (task 2.4)."""
        ICP = self.env['ir.config_parameter'].sudo()
        ICP.set_param('ai_agent_core.validate_tool_args', 'False')
        try:
            loop = self._loop()
            loop.env = self.env
            out = loop._validate_required_args(self.tools['okf_search'], {})
            self.assertEqual(out, '', 'avstängd grind ska släppa igenom')
        finally:
            ICP.set_param('ai_agent_core.validate_tool_args', 'True')


@tagged('post_install', '-at_install', 'tool_quality')
class TestRepeatGuard(common.TransactionCase):
    """Task 3: upprepade identiska anrop som redan felat stoppas."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from odoo.addons.ai_agent_core.core.tools import builtin_tools
        from odoo.addons.ai_agent_core.core.loop import AgentLoop
        cls.tools = {t.name: t for t in builtin_tools()}
        cls.Loop = AgentLoop

    def test_call_key_is_stable(self):
        key1 = self.Loop._call_key('odoo_search', {'a': 1, 'b': 2})
        key2 = self.Loop._call_key('odoo_search', {'b': 2, 'a': 1})
        self.assertEqual(key1, key2, 'nyckeln ska vara ordningsoberoende')

    def test_changed_args_get_a_new_key(self):
        key1 = self.Loop._call_key('okf_search', {})
        key2 = self.Loop._call_key('okf_search', {'query': 'mail'})
        self.assertNotEqual(key1, key2)


@tagged('post_install', '-at_install', 'tool_quality')
class TestDescribeAvailable(common.TransactionCase):
    """Task 4: "vad finns" utan ir.model-sökning."""

    def _call(self, question):
        from odoo.addons.ai_agent_core.core.tools import (
            _tool_describe_available)
        return json.loads(_tool_describe_available(self.env, question))

    def test_empty_question_is_rejected(self):
        data = self._call('')
        self.assertIn('error', data)
        self.assertEqual(data['parameter'], 'question')

    def test_mail_question_names_the_mail_model(self):
        """Frågan "mail" ska hitta modellen som håller mailen."""
        data = self._call('mail från en kund')
        models = [m['model'] for m in data.get('models', [])]
        self.assertTrue(models, 'minst en modell ska föreslås')
        # Modellen som faktiskt håller mail-triage ska finnas med.
        self.assertTrue(
            any('mail' in m for m in models),
            'en mail-modell ska föreslås, fick %s' % models)

    def test_fields_are_grouped_by_dimension(self):
        data = self._call('mail')
        for entry in data.get('models', []):
            fields = entry.get('fields') or {}
            self.assertIsInstance(fields, dict)
            for dim in fields:
                self.assertIn(dim, ('content', 'time', 'customer',
                                    'status', 'action'))

    def test_no_match_is_honest(self):
        data = self._call('zzzznonsenseqqqq')
        self.assertEqual(data.get('models', []), [])
        self.assertIn('note', data)

    def test_budget_is_respected(self):
        data = self._call('mail projekt kund uppgift')
        blob = json.dumps(data.get('models', []))
        self.assertLessEqual(len(blob), 1600,
                             'svaret ska rymmas i kontextbudgeten')
        self.assertLessEqual(len(data.get('models', [])), 5)


@tagged('post_install', '-at_install', 'tool_quality')
class TestImportance(common.TransactionCase):
    """Task 5: vikt är inte samma sak som intresse."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Mixin = cls.env['ai.okf.mixin']

    def test_no_registration_gives_zero(self):
        """Utan registrering gissar vi inte."""
        partner = self.env['res.partner'].create({'name': 'Vikttest'})
        score, reasons = self.Mixin._okf_importance_of(partner)
        self.assertEqual(score, 0.0)
        self.assertEqual(reasons, [])

    def test_registered_signals_add_up(self):
        self.Mixin._okf_register_importance('res.partner', {
            'customer': 'is_company'})
        partner = self.env['res.partner'].create({
            'name': 'Viktigt bolag', 'is_company': True})
        score, reasons = self.Mixin._okf_importance_of(partner)
        self.assertGreater(score, 0)
        self.assertIn('har kund', reasons)

    def test_action_outweighs_due(self):
        """Ett objekt som kräver åtgärd väger tyngre än en deadline."""
        self.assertGreater(
            self.Mixin.IMPORTANCE_WEIGHTS['action_required'],
            self.Mixin.IMPORTANCE_WEIGHTS['due'])

    def test_interest_score_is_not_included(self):
        """Intressepoängen får ALDRIG räknas in i vikten (D5)."""
        self.Mixin._okf_register_importance('res.partner', {
            'customer': 'is_company'})
        partner = self.env['res.partner'].create({
            'name': 'Bara intresse', 'is_company': False})
        score, _reasons = self.Mixin._okf_importance_of(partner)
        self.assertEqual(score, 0.0,
                         'en post utan signaler har ingen vikt, oavsett '
                         'hur intressant den än är')

    def test_mail_bridge_registers_signals(self):
        """Bryggan ska ha anmält sina signaler."""
        if 'user_mail_ai.mail' not in self.env:
            self.skipTest('user_mail_ai saknas')
        signals = self.Mixin._okf_importance_signals()
        self.assertIn('user_mail_ai.mail', signals)
        cfg = signals['user_mail_ai.mail']
        self.assertEqual(cfg.get('action_required'), 'action_needed')
        self.assertEqual(cfg.get('customer'), 'partner_id')
