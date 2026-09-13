# -*- coding: utf-8 -*-
"""Tester för write-verify (improve-ai-coworker-memory-and-tools grupp 3).

Verifierar att:
  (3.1) ett verifieringsavtal kan deklareras på ai.tool och läses in,
  (3.2) verifieringen läser tillbaka nyckelfält och ger pass/fail,
  (3.3) verktyg utan avtal körs utan write-verify utan fel,
  (3.4) beskrivningsmallen anger verifierbart utfall och felvägledning.
"""

import json

from odoo.tests import common, tagged

from odoo.addons.ai_agent_core.core.verify import (
    verify_write_outcome, ValidationStatus,
)


@tagged('post_install')
class TestWriteVerify(common.TransactionCase):
    """Deklarativt verifieringsavtal + write-verify."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Tool = cls.env['ai.tool']

    # ── 3.1 Avtalet ────────────────────────────────────────────────────

    def test_contract_parsed_from_json(self):
        """Avtal i XML/UI läses in utan kärnkodändring."""
        tool = self.Tool.create({
            'name': 'verify_test_tool',
            'description': 'test',
            'verification_enabled': True,
            'verification_json': json.dumps({
                'model': 'res.partner',
                'id_path': 'id',
                'checks': [{'field': 'name', 'equals_path': 'values.name'}],
            }),
        })
        contract = tool.get_verification_contract()
        self.assertEqual(contract['model'], 'res.partner')
        self.assertEqual(len(contract['checks']), 1)

    def test_contract_empty_when_disabled(self):
        """Avtal utan enabled → tomt (ingen write-verify)."""
        tool = self.Tool.create({
            'name': 'verify_disabled_tool',
            'description': 'test',
            'verification_json': '{"checks": [{"field": "name"}]}',
        })
        self.assertEqual(tool.get_verification_contract(), {})

    def test_contract_invalid_json_is_tolerated(self):
        """Ogiltig JSON → tomt avtal, ingen krasch."""
        tool = self.Tool.create({
            'name': 'verify_bad_json_tool',
            'description': 'test',
            'verification_enabled': True,
            'verification_json': '{not valid json',
        })
        self.assertEqual(tool.get_verification_contract(), {})

    # ── 3.2 Verifiering ────────────────────────────────────────────────

    def test_verify_pass_when_outcome_matches(self):
        """Utfall som stämmer → pass."""
        partner = self.env['res.partner'].create({'name': 'Verify OK'})
        contract = {
            'model': 'res.partner', 'id_path': 'id',
            'checks': [{'field': 'name', 'equals_path': 'values.name'}],
        }
        result = verify_write_outcome(
            contract, {'ok': True, 'id': partner.id,
                       'values': {'name': 'Verify OK'}},
            env=self.env)
        self.assertEqual(result.status, ValidationStatus.PASS)
        self.assertTrue(result.passed)

    def test_verify_fail_when_outcome_differs(self):
        """Avvikande nyckelfält → fail med fältangivelse."""
        partner = self.env['res.partner'].create({'name': 'Actual Name'})
        contract = {
            'model': 'res.partner', 'id_path': 'id',
            'checks': [{'field': 'name', 'equals_path': 'values.name'}],
        }
        result = verify_write_outcome(
            contract, {'ok': True, 'id': partner.id,
                       'values': {'name': 'Intended Name'}},
            env=self.env)
        self.assertEqual(result.status, ValidationStatus.FAIL)
        self.assertTrue(result.needs_fix)
        self.assertEqual(result.all_errors[0].field, 'name')
        self.assertTrue(result.fix_suggestions)

    def test_verify_fail_when_record_missing(self):
        """Id som inte existerar → fail."""
        contract = {
            'model': 'res.partner', 'id_path': 'id',
            'checks': [{'field': 'name', 'non_empty': True}],
        }
        result = verify_write_outcome(
            contract, {'ok': True, 'id': 999999999}, env=self.env)
        self.assertEqual(result.status, ValidationStatus.FAIL)

    def test_verify_non_empty_check(self):
        """non_empty-check fångar tomt fält (t.ex. document.page content)."""
        partner = self.env['res.partner'].create({'name': 'NonEmpty'})
        contract = {
            'model': 'res.partner', 'id_path': 'id',
            'checks': [{'field': 'email', 'non_empty': True}],
        }
        result = verify_write_outcome(
            contract, {'ok': True, 'id': partner.id}, env=self.env)
        self.assertEqual(result.status, ValidationStatus.FAIL)
        self.assertEqual(result.all_errors[0].field, 'email')

    def test_verify_no_contract_passes(self):
        """Inget avtal → pass (ingen write-verify)."""
        result = verify_write_outcome({}, {'ok': True, 'id': 1},
                                      env=self.env)
        self.assertTrue(result.passed)

    # ── 3.3/3.4 Coworker-integration + beskrivning ─────────────────────

    def test_tool_without_contract_skipped(self):
        """Verktyg utan avtal ger inga utfall (ingen write-verify)."""
        self.Tool.create({
            'name': 'no_contract_tool', 'description': 'x',
        })
        coworker = self.env['ai.coworker'].create({
            'name': 'WV Coworker', 'description': 'x', 'status': 'active',
        })
        outcomes = coworker._run_write_verify(
            [('no_contract_tool', {}, json.dumps({'ok': True, 'id': 1}))])
        self.assertEqual(outcomes, [])

    def test_tool_with_contract_produces_outcome(self):
        """Verktyg med avtal ger ett verifieringsutfall."""
        self.Tool.create({
            'name': 'with_contract_tool', 'description': 'x',
            'verification_enabled': True,
            'verification_json': json.dumps({
                'model': 'res.partner', 'id_path': 'id',
                'checks': [{'field': 'name', 'non_empty': True}],
            }),
        })
        partner = self.env['res.partner'].create({'name': 'Outcome'})
        coworker = self.env['ai.coworker'].create({
            'name': 'WV Coworker 2', 'description': 'x', 'status': 'active',
        })
        outcomes = coworker._run_write_verify(
            [('with_contract_tool', {},
              json.dumps({'ok': True, 'id': partner.id}))])
        self.assertEqual(len(outcomes), 1)
        self.assertEqual(outcomes[0]['tool'], 'with_contract_tool')

    def test_description_mentions_verifiable_outcome(self):
        """Beskrivningsmallen nämner verifierbart utfall och felvägledning."""
        help_text = self.Tool._fields['description'].help or ''
        self.assertIn('verifierbart utfall', help_text)
        self.assertIn('felvägledning', help_text)
        self.assertIn('guardrail', help_text)
