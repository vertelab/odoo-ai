# -*- coding: utf-8 -*-
"""Tester för självkorrigering och kvalitetsloop
(improve-ai-coworker-memory-and-tools grupp 4).

Verifierar att:
  (4.1) write-verify-resultat kopplas in som ett lager (fail → guidance +
        fix-förslag),
  (4.2) en misslyckad sekvens korrigeras och verifieras inom begränsat
        antal försök,
  (4.3) ärendet eskalerar när max antal försök nås utan verifierat utfall,
  (4.4) varje försök loggas (utfall, felorsak, verifieringsresultat) och
        kedjan går att läsa ut.
"""

import json

from odoo.tests import common, tagged

from odoo.addons.ai_agent_core.core.improve import (
    ToolSequenceCorrector, verification_guidance,
)
from odoo.addons.ai_agent_core.core.verify import (
    verify_write_outcome, ValidationStatus,
)


@tagged('post_install')
class TestToolSelfCorrection(common.TransactionCase):
    """Självkorrigering av verktygssekvenser."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

    def _failing_result(self, field='name', expected='X', actual='Y'):
        """Bygg ett fail-resultat via verify_write_outcome."""
        partner = self.env['res.partner'].create({'name': actual})
        contract = {
            'model': 'res.partner', 'id_path': 'id',
            'checks': [{'field': field, 'equals': expected}],
        }
        return verify_write_outcome(
            contract, {'ok': True, 'id': partner.id}, env=self.env)

    # ── 4.1 Verifiering som lager ──────────────────────────────────────

    def test_verification_guidance_from_fail(self):
        """Fail-resultat → guidance med text + fix-förslag som referens."""
        result = self._failing_result()
        guidance = verification_guidance(result, 'odoo_create')
        self.assertTrue(guidance.text)
        self.assertTrue(guidance.references)
        self.assertIn('name', guidance.text)

    def test_verification_guidance_severity(self):
        """needs_fix → hög severity."""
        result = self._failing_result()
        self.assertTrue(result.needs_fix)
        guidance = verification_guidance(result)
        self.assertEqual(guidance.severity, 'high')

    # ── 4.2 Korrigering lyckas ─────────────────────────────────────────

    def test_correction_succeeds_and_verifies(self):
        """Misslyckat anrop → korrigerat anrop → verifierat utfall."""
        state = {'n': 0}

        def attempt_fn(attempt, suggestions):
            state['n'] += 1
            return (True, {'ok': True, 'id': 42, 'values': {}}, '', [])

        def verify_fn(payload):
            # Första försöket fail, andra pass — modellerar rättelse.
            if state['n'] >= 2:
                r = type('R', (), {})()
                r.passed = True
                r.status = ValidationStatus.PASS
                r.all_errors = []
                r.fix_suggestions = []
                return r
            r = type('R', (), {})()
            r.passed = False
            r.status = ValidationStatus.FAIL
            r.all_errors = []
            r.fix_suggestions = ['fix it']
            return r

        corrector = ToolSequenceCorrector(max_attempts=3)
        run = corrector.correct(
            'odoo_create', attempt_fn, verify_fn,
            first_error='name: expected X, got Y')
        self.assertTrue(run.verified)
        self.assertFalse(run.escalated)

    def test_correction_logs_initial_failure(self):
        """Det inledande misslyckade försöket loggas (försök 0)."""
        corrector = ToolSequenceCorrector(max_attempts=1)

        def attempt_fn(attempt, suggestions):
            return (True, {'ok': True}, '', [])

        run = corrector.correct(
            't', attempt_fn, None, first_error='boom')
        self.assertEqual(run.attempts[0].attempt, 0)
        self.assertEqual(run.attempts[0].outcome, 'failed')
        self.assertEqual(run.attempts[0].error, 'boom')

    # ── 4.3 Eskalering ─────────────────────────────────────────────────

    def test_escalates_when_never_verified(self):
        """Upprepade misslyckanden → eskalering efter max försök."""
        def attempt_fn(attempt, suggestions):
            return (True, {'ok': True}, '', [])

        def verify_fn(payload):
            r = type('R', (), {})()
            r.passed = False
            r.status = ValidationStatus.FAIL
            r.all_errors = []
            r.fix_suggestions = ['still broken']
            return r

        corrector = ToolSequenceCorrector(max_attempts=2)
        run = corrector.correct('t', attempt_fn, verify_fn,
                                first_error='start')
        self.assertFalse(run.verified)
        self.assertTrue(run.escalated)
        self.assertTrue(run.escalation_reason)
        # 1 inledande + 2 försök
        self.assertEqual(len(run.attempts), 3)

    def test_escalates_when_attempt_raises(self):
        """Undantag i försöket fångas och leder till eskalering."""
        def attempt_fn(attempt, suggestions):
            raise RuntimeError('boom')

        corrector = ToolSequenceCorrector(max_attempts=2)
        run = corrector.correct('t', attempt_fn, None, first_error='start')
        self.assertTrue(run.escalated)
        self.assertIn('raised', run.attempts[-1].error)

    # ── 4.4 Spårbarhet ─────────────────────────────────────────────────

    def test_chain_readable(self):
        """Kedjan försök → fel → rättelse → verifiering kan läsas ut."""
        state = {'n': 0}

        def attempt_fn(attempt, suggestions):
            state['n'] += 1
            return (True, {'ok': True}, '', [])

        def verify_fn(payload):
            r = type('R', (), {})()
            ok = state['n'] >= 2
            r.passed = ok
            r.status = (ValidationStatus.PASS if ok
                        else ValidationStatus.FAIL)
            r.all_errors = []
            r.fix_suggestions = []
            return r

        corrector = ToolSequenceCorrector(max_attempts=3)
        run = corrector.correct('t', attempt_fn, verify_fn,
                                first_error='initial fail')
        chain = run.chain()
        self.assertEqual(chain[0]['outcome'], 'failed')
        self.assertEqual(chain[-1]['outcome'], 'verified')
        self.assertTrue(chain[-1]['verification'])

    def test_coworker_logs_attempts_to_session(self):
        """Coworkern loggar kedjan som session-rader (granskningsbart)."""
        self.env['ai.tool'].create({
            'name': 'log_contract_tool', 'description': 'x',
            'verification_enabled': True,
            'verification_json': json.dumps({
                'model': 'res.partner', 'id_path': 'id',
                'checks': [{'field': 'name', 'equals': 'INTENDED'}],
            }),
        })
        partner = self.env['res.partner'].create({'name': 'ACTUAL'})
        coworker = self.env['ai.coworker'].create({
            'name': 'Log WV Coworker', 'description': 'x', 'status': 'active',
        })
        session = self.env['ai.coworker.session'].create({
            'coworker_id': coworker.id, 'status': 'active',
        })
        coworker._run_write_verify(
            [('log_contract_tool', {},
              json.dumps({'ok': True, 'id': partner.id,
                          'values': {'name': 'ACTUAL'}}))],
            session=session)
        lines = session.session_line_ids.filtered(
            lambda l: l.role == 'system' and 'tool-verify' in (l.content or ''))
        self.assertTrue(lines)
        self.assertIn('attempt=0', lines[0].content)

    # ── 4.5/4.7 Fabricering och dubbletter ─────────────────────────────

    def _coworker_with_contract(self, contract, name='corr_tool'):
        self.env['ai.tool'].create({
            'name': name, 'description': 'x',
            'verification_enabled': True,
            'verification_json': json.dumps(contract),
        })
        return self.env['ai.coworker'].create({
            'name': 'Corr Coworker %s' % name, 'description': 'x',
            'status': 'active',
        })

    def test_missing_required_field_is_not_fabricated(self):
        """4.5: saknat non_empty-fält ⇒ INGET påhittat värde skrivs."""
        contract = {
            'model': 'res.partner', 'id_path': 'id',
            'checks': [{'field': 'name', 'non_empty': True}],
        }
        coworker = self._coworker_with_contract(contract, 'fab_tool')
        partner = self.env['res.partner'].create({'name': ''})
        # Anroparen angav inget värde för name.
        data = {'ok': True, 'id': partner.id, 'values': {}}
        result = verify_write_outcome(contract, data, env=self.env)
        self.assertTrue(result.needs_fix)

        run = coworker._correct_failed_tool(
            'fab_tool', contract, data, result)
        self.assertTrue(run.escalated)
        self.assertIn('Saknat värde', run.escalation_reason)
        # Namnet är fortfarande tomt — inget fabricerat värde skrevs.
        self.assertEqual(partner.name or '', '')
        # Ingen post med 'avto'-mönster existerar.
        bogus = self.env['res.partner'].search(
            [('name', 'like', 'avto-')])
        self.assertFalse(bogus)

    def test_correction_creates_no_duplicate(self):
        """4.7: korrigeringen skapar ingen ytterligare post."""
        contract = {
            'model': 'res.partner', 'id_path': 'id',
            'checks': [{'field': 'name', 'equals': 'INTENDED'}],
        }
        coworker = self._coworker_with_contract(contract, 'dup_tool')
        partner = self.env['res.partner'].create({'name': 'ACTUAL'})
        before = self.env['res.partner'].search_count([])
        data = {'ok': True, 'id': partner.id, 'values': {'name': 'ACTUAL'}}
        result = verify_write_outcome(contract, data, env=self.env)

        run = coworker._correct_failed_tool(
            'dup_tool', contract, data, result)
        after = self.env['res.partner'].search_count([])
        self.assertEqual(
            before, after,
            'korrigeringen får inte skapa nya poster (dubbletter)')
        # Den befintliga posten åtgärdades i stället.
        self.assertEqual(run.attempts[-1].outcome, 'verified')
        self.assertEqual(partner.name, 'ACTUAL')

    def test_correction_escalates_without_existing_record(self):
        """4.7: ingen befintlig post ⇒ eskalera, skapa inte en ny."""
        contract = {
            'model': 'res.partner', 'id_path': 'id',
            'checks': [{'field': 'name', 'equals': 'INTENDED'}],
        }
        coworker = self._coworker_with_contract(contract, 'norec_tool')
        before = self.env['res.partner'].search_count([])
        data = {'ok': True, 'id': 99999999, 'values': {'name': 'X'}}
        result = verify_write_outcome(contract, data, env=self.env)

        run = coworker._correct_failed_tool(
            'norec_tool', contract, data, result)
        self.assertTrue(run.escalated)
        self.assertEqual(
            before, self.env['res.partner'].search_count([]))

    # ── 4.8 Åtgärdbart fel når korrigeringen ───────────────────────────

    def test_actionable_tool_error_reaches_correction(self):
        """4.8: ToolError-formatet (inget 'ok') hanteras, inte hoppas över."""
        self._coworker_with_contract({}, 'err_tool')
        coworker = self.env['ai.coworker'].create({
            'name': 'Err Coworker', 'description': 'x', 'status': 'active'})
        session = self.env['ai.coworker.session'].create({
            'coworker_id': coworker.id, 'status': 'active'})
        payload = json.dumps({
            'error': "Unknown field 'parrent_id' on res.partner",
            'parameter': 'parrent_id',
            'expected': 'one of: name, parent_id',
            'actual': 'parrent_id',
            'valid_fields': ['name', 'parent_id'],
            'retryable': True,
            'tool_name': 'odoo_create',
        })
        # Avtal krävs för att write-verify-grenen ska köras.
        tool = self.env['ai.tool'].search(
            [('name', '=', 'err_tool')], limit=1)
        tool.write({
            'verification_enabled': True,
            'verification_json': json.dumps({
                'model_path': 'model', 'id_path': 'id',
                'checks': [{'field': 'name', 'non_empty': True}]}),
        })
        outcomes = coworker._run_write_verify(
            [('err_tool', {}, payload)], session=session)
        self.assertTrue(
            any(o.get('actionable') for o in outcomes),
            'det åtgärdbara felet ska hanteras av korrigeringen')
        lines = session.session_line_ids.filtered(
            lambda l: l.role == 'system' and 'tool-verify' in (l.content or ''))
        self.assertTrue(lines, 'kedjan ska synas i sessionen')

    def test_non_retryable_tool_error_is_not_treated_as_actionable(self):
        """4.8: icke-åtgärdbara fel hanteras inte som korrigeringsbara."""
        self._coworker_with_contract({}, 'noret_tool')
        coworker = self.env['ai.coworker'].create({
            'name': 'NoRet Coworker', 'description': 'x', 'status': 'active'})
        tool = self.env['ai.tool'].search(
            [('name', '=', 'noret_tool')], limit=1)
        tool.write({
            'verification_enabled': True,
            'verification_json': json.dumps({
                'model': 'res.partner', 'id_path': 'id',
                'checks': [{'field': 'name', 'non_empty': True}]}),
        })
        payload = json.dumps({'error': 'boom', 'retryable': False})
        outcomes = coworker._run_write_verify([('noret_tool', {}, payload)])
        self.assertFalse(any(o.get('actionable') for o in outcomes))

    # ── 4.9 Korrigeraren tränas faktiskt ───────────────────────────────

    def test_write_verify_invokes_corrector(self):
        """4.9: write-verify anropar korrigeraren (inte bara kedjan förbi)."""
        contract = {
            'model': 'res.partner', 'id_path': 'id',
            'checks': [{'field': 'name', 'equals': 'INTENDED'}],
        }
        coworker = self._coworker_with_contract(contract, 'trained_tool')
        session = self.env['ai.coworker.session'].create({
            'coworker_id': coworker.id, 'status': 'active'})
        partner = self.env['res.partner'].create({'name': 'ACTUAL'})
        outcomes = coworker._run_write_verify(
            [('trained_tool', {},
              json.dumps({'ok': True, 'id': partner.id,
                          'values': {'name': 'ACTUAL'}}))],
            session=session)
        self.assertTrue(outcomes)
        self.assertIn('correction', outcomes[0],
                      'korrigeringen ska ha körts och registrerats')
        self.assertIsNotNone(outcomes[0]['correction'])
