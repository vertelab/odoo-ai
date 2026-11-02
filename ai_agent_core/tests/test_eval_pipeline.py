# -*- coding: utf-8 -*-
"""Tester för eval-persistensen (wire-orphaned-core-mechanisms 3–6).

Verifierar att `core/eval.py` har en anropare och en persistens:

  3.x  ai.eval.case / ai.eval.run / ai.eval.result + access-regler
  4.x  befordran session → eval-fall (HITL, ingen automatisk väg)
  5.x  eval-cron (budget-grind, falltak, accuracy → kaizen)
  6.x  kaizen-fynd när accuracy sjunker

Loopen mockas: testerna mäter WIRINGEN, inte modellens svar. Ett riktigt
provider-anrop hade gjort testet långsamt, icke-deterministiskt och
beroende av en nyckel — och hade inte sagt något om det som faktiskt
testas (att rätt poster skapas på rätt ställe).
"""

from unittest.mock import AsyncMock, patch

from odoo.tests.common import TransactionCase, tagged
from odoo.exceptions import AccessError, ValidationError


@tagged('post_install', '-at_install')
class EvalTestBase(TransactionCase):
    """Gemensam uppsättning: medarbetare, agent, modell, session."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Provider = cls.env['ai.provider']
        cls.Model = cls.env['ai.model']
        cls.Agent = cls.env['ai.agent']
        cls.Coworker = cls.env['ai.coworker']
        cls.CoworkerAgent = cls.env['ai.coworker.agent']
        cls.Session = cls.env['ai.coworker.session']
        cls.Line = cls.env['ai.coworker.session.line']
        cls.Case = cls.env['ai.eval.case']
        cls.Run = cls.env['ai.eval.run']

        cls.provider = cls.Provider.create({
            'name': 'Eval Test Provider',
            'provider_type': 'custom',
            'status': 'confirmed',
        })
        cls.model = cls.Model.create({
            'name': 'eval-test-model',
            'provider': cls.provider.id,
        })

    def _coworker(self, name):
        coworker = self.Coworker.create({
            'name': name,
            'description': 'Eval test coworker',
            'status': 'active',
        })
        agent = self.Agent.create({
            'name': f'{name} agent',
            'ai_role': 'Test',
            'model_id': self.model.id,
        })
        self.CoworkerAgent.create({
            'coworker_id': coworker.id,
            'agent_id': agent.id,
        })
        return coworker

    def _session(self, coworker, finish_reason='error', user_text='Vad är 2+2?'):
        session = self.Session.create({
            'coworker_id': coworker.id,
            'status': 'error' if finish_reason == 'error' else 'done',
            'finish_reason': finish_reason,
        })
        if user_text is not None:
            self.Line.create({
                'session_id': session.id,
                'sequence': 1,
                'role': 'user',
                'content': user_text,
            })
        return session

    def _case(self, coworker, session=None, expected_contains='["4"]'):
        session = session or self._session(coworker)
        return self.Case.create({
            'coworker_id': coworker.id,
            'source_session_id': session.id,
            'input': 'Vad är 2+2?',
            'expected_contains': expected_contains,
        })


@tagged('post_install', '-at_install')
class TestEvalModels(EvalTestBase):
    """3.1–3.5: modellerna och deras access-regler."""

    def test_case_fields_roundtrip(self):
        """3.1: fallet bär medarbetare, källa, input och förväntan."""
        coworker = self._coworker('Eval fall')
        session = self._session(coworker)
        case = self._case(coworker, session)
        self.assertEqual(case.coworker_id, coworker)
        self.assertEqual(case.source_session_id, session)
        self.assertEqual(case.input, 'Vad är 2+2?')
        self.assertTrue(case.active)

    def test_case_rejects_broken_expected_list(self):
        """3.1: en trasig förväntan avvisas — annars blir testet tyst grönt."""
        coworker = self._coworker('Eval trasig förväntan')
        session = self._session(coworker)
        with self.assertRaises(ValidationError):
            self.Case.create({
                'coworker_id': coworker.id,
                'source_session_id': session.id,
                'input': 'x',
                'expected_contains': 'inte json',
            })

    def test_case_to_eval_case(self):
        """3.1: JSON-listorna blir `core.eval.EvalCase`-fält."""
        coworker = self._coworker('Eval konvertering')
        case = self._case(
            coworker, expected_contains='["4", "fyra"]')
        core_case = case._to_eval_case()
        self.assertEqual(core_case.input, 'Vad är 2+2?')
        self.assertEqual(core_case.expected_contains, ['4', 'fyra'])
        self.assertEqual(core_case.expected_not_contains, [])
        self.assertEqual(core_case.expected_output, '')

    def test_run_and_result_persist(self):
        """3.2/3.3: ett run bär aggregeringen, resultaten bär per fall."""
        coworker = self._coworker('Eval run')
        case = self._case(coworker)
        run = self.Run.create({
            'coworker_id': coworker.id,
            'accuracy': 0.5,
            'passed': 1,
            'failed': 1,
            'total_cases': 2,
            'total_cost': 0.01,
            'result_ids': [(0, 0, {
                'case_id': case.id,
                'passed': True,
                'score': 1.0,
                'actual_output': '4',
                'latency_ms': 12.0,
            })],
        })
        self.assertEqual(len(run.result_ids), 1)
        self.assertEqual(run.result_ids.case_id, case)
        self.assertTrue(run.result_ids.passed)

    def test_kaizen_report_has_eval_accuracy_field(self):
        """3.4: fältet finns och är tomt (0.0) på en ny rapport."""
        coworker = self._coworker('Eval kaizen-fält')
        report = self.env['ai.kaizen.report'].create({
            'coworker_id': coworker.id,
            'week_start': '2026-01-05',
            'week_end': '2026-01-11',
        })
        self.assertEqual(report.eval_accuracy_trend, 0.0)

    def test_user_can_read_but_not_unlink(self):
        """3.5: vanlig användare läser, systemgruppen raderar."""
        user = self.env['res.users'].create({
            'name': 'Eval läsare',
            'login': 'eval_reader_test',
            'groups_id': [(6, 0, [self.env.ref('base.group_user').id])],
        })
        case = self._case(self._coworker('Eval access'))
        as_user = case.with_user(user)
        self.assertEqual(as_user.read(['input'])[0]['input'], 'Vad är 2+2?')
        with self.assertRaises(AccessError):
            as_user.unlink()


@tagged('post_install', '-at_install')
class TestPromoteToEvalCase(EvalTestBase):
    """4.1–4.4: befordran är HITL och kräver en riktig fråga."""

    def test_promote_uses_first_user_line(self):
        """4.1: input = sessionens FÖRSTA user-rad, källan = sessionen."""
        coworker = self._coworker('Befordran')
        session = self._session(coworker, user_text='Första frågan')
        self.Line.create({
            'session_id': session.id,
            'sequence': 2,
            'role': 'assistant',
            'content': 'Första svaret',
        })
        self.Line.create({
            'session_id': session.id,
            'sequence': 3,
            'role': 'user',
            'content': 'Andra frågan',
        })
        session.action_promote_to_eval_case()
        case = self.Case.search([('source_session_id', '=', session.id)])
        self.assertEqual(len(case), 1)
        self.assertEqual(case.input, 'Första frågan')
        self.assertEqual(case.coworker_id, coworker)
        self.assertEqual(case.category, 'error')

    def test_promote_without_user_line_raises(self):
        """4.2: en session utan fråga kan inte bli ett testfall."""
        coworker = self._coworker('Befordran utan fråga')
        session = self._session(coworker, user_text=None)
        with self.assertRaises(ValidationError):
            session.action_promote_to_eval_case()
        self.assertFalse(
            self.Case.search([('source_session_id', '=', session.id)]))

    def test_promote_twice_raises(self):
        """4.1: en session befordras en gång (UNIQUE på källan)."""
        coworker = self._coworker('Befordran dubbel')
        session = self._session(coworker)
        session.action_promote_to_eval_case()
        with self.assertRaises(ValidationError):
            session.action_promote_to_eval_case()

    def test_promote_returns_form_action(self):
        """4.3: knappen öppnar det nya fallet — förväntan fylls i av människa."""
        coworker = self._coworker('Befordran action')
        session = self._session(coworker)
        action = session.action_promote_to_eval_case()
        self.assertEqual(action['res_model'], 'ai.eval.case')
        case = self.Case.browse(action['res_id'])
        self.assertFalse(case.expected_contains,
                         'förväntan får inte hittas på — den sätts av en människa')

    def test_no_automatic_promotion(self):
        """4.4: ingen kodväg anropar befordran utanför knappen."""
        import pathlib
        root = pathlib.Path(__file__).resolve().parent.parent
        callers = []
        for path in root.rglob('*.py'):
            if 'tests' in path.parts or '__pycache__' in path.parts:
                continue
            text = path.read_text()
            if 'action_promote_to_eval_case' not in text:
                continue
            for lineno, line in enumerate(text.splitlines(), 1):
                stripped = line.strip()
                if 'action_promote_to_eval_case' not in stripped:
                    continue
                # Definitionen själv är inte ett anrop.
                if stripped.startswith('def '):
                    continue
                callers.append(f'{path.relative_to(root)}:{lineno}')
        self.assertEqual(
            callers, [],
            'Befordran får bara ske från knappen (HITL). Anrop hittade: '
            + ', '.join(callers))


@tagged('post_install', '-at_install')
class TestCronRunEvals(EvalTestBase):
    """5.1–5.5: cronen är den enda anroparen av AgentEvaluator."""

    def _patched_coworker(self):
        """Medarbetare vars `_run_eval_cases` är mockad på KLASSEN.

        Mocken läggs på typen, inte instansen: `self.search()` returnerar
        nya browses, så en instanspatch hade aldrig träffat.
        """
        coworker = self._coworker('Eval cron')
        return coworker, patch.object(
            type(coworker), '_run_eval_cases',
            autospec=True,
            side_effect=lambda rec, cases: self.Run.create({
                'coworker_id': rec.id,
                'accuracy': 0.5,
                'passed': 1,
                'failed': 1,
                'total_cases': len(cases),
            }))

    def test_run_created_for_active_coworker_with_cases(self):
        """5.1: aktiv medarbetare med aktiva fall ⇒ ett run skapas."""
        coworker, mocked = self._patched_coworker()
        self._case(coworker)
        with mocked as run_mock:
            result = self.Coworker._cron_run_evals()
        self.assertEqual(run_mock.call_count, 1)
        self.assertGreaterEqual(result['runs'], 1)
        self.assertTrue(self.Run.search([('coworker_id', '=', coworker.id)]))

    def test_skips_when_budget_exhausted(self):
        """5.2: budget_exhausted ⇒ inget run skapas."""
        coworker, mocked = self._patched_coworker()
        self._case(coworker)
        with mocked as run_mock, \
                patch.object(type(coworker), 'budget_exhausted',
                             new_callable=lambda: property(lambda s: True)):
            self.Coworker._cron_run_evals()
        self.assertEqual(run_mock.call_count, 0)
        self.assertFalse(self.Run.search([('coworker_id', '=', coworker.id)]))

    def test_max_cases_limits_the_run(self):
        """5.3: 25 fall med taket 20 ⇒ 20 fall skickas till körningen."""
        coworker, mocked = self._patched_coworker()
        for index in range(25):
            self._case(coworker, expected_contains=f'["{index}"]')
        self.env['ir.config_parameter'].sudo().set_param(
            'ai_agent_core.eval_max_cases', '20')
        with mocked as run_mock:
            self.Coworker._cron_run_evals()
        self.assertEqual(run_mock.call_count, 1)
        cases = run_mock.call_args[0][1]
        self.assertEqual(len(cases), 20)

    def test_max_cases_default_is_twenty(self):
        """5.3: utan parameter gäller 20."""
        self.env['ir.config_parameter'].sudo().search(
            [('key', '=', 'ai_agent_core.eval_max_cases')]).unlink()
        self.assertEqual(self.Coworker._eval_max_cases(), 20)

    def test_accuracy_written_to_kaizen_report(self):
        """5.5: runnets accuracy hamnar på veckorapporten."""
        coworker = self._coworker('Eval kaizen-skrivning')
        report = self.env['ai.kaizen.report'].create({
            'coworker_id': coworker.id,
            'week_start': '2026-01-05',
            'week_end': '2026-01-11',
        })
        run = self.Run.create({
            'coworker_id': coworker.id,
            'accuracy': 0.75,
            'passed': 3,
            'failed': 1,
            'total_cases': 4,
        })
        run._write_accuracy_to_kaizen()
        self.assertAlmostEqual(report.eval_accuracy_trend, 0.75)

    def test_run_eval_cases_uses_mocked_loop(self):
        """5.1: `_run_eval_cases` bygger loopen och persisterar resultatet."""
        coworker = self._coworker('Eval loop')
        case = self._case(coworker)
        fake_loop = object()
        fake_run = self.env['ai.eval.run'].browse()

        with patch.object(type(coworker), '_build_loop',
                          autospec=True,
                          return_value=fake_loop) as build_loop, \
                patch.object(type(coworker), '_session_tools',
                             autospec=True,
                             return_value=({}, set())), \
                patch('odoo.addons.ai_agent_core.core.eval.EvalRunner.run_eval',
                      new=AsyncMock(return_value=_fake_eval_run(case))), \
                patch('odoo.addons.ai_agent_core.core.provider.'
                      'ProviderFactory.from_coworker',
                      return_value=(_FakeProvider(), self.model)):
            fake_run = coworker._run_eval_cases(case)

        self.assertTrue(build_loop.called)
        self.assertEqual(fake_run.total_cases, 1)
        self.assertEqual(len(fake_run.result_ids), 1)
        self.assertEqual(fake_run.result_ids.case_id, case)
        self.assertTrue(fake_run.result_ids.passed)


class _FakeProvider:
    """Provider-stubbe: eval-testerna rör aldrig nätverket."""

    async def aclose(self):
        return None


def _fake_eval_run(case):
    """Ett `core.eval.EvalRun` med ett godkänt resultat för `case`."""
    from odoo.addons.ai_agent_core.core.eval import (
        EvalResult, EvalRun)

    core_case = case._to_eval_case()
    result = EvalResult(
        case=core_case,
        actual_output='4',
        passed=True,
        score=1.0,
        latency_ms=5.0,
    )
    return EvalRun(
        agent_name='eval-test',
        model='eval-test-model',
        total_cases=1,
        passed=1,
        failed=0,
        accuracy=1.0,
        avg_score=1.0,
        results=[result],
    )


@tagged('post_install', '-at_install')
class TestKaizenEvalFinding(EvalTestBase):
    """6.1–6.3: sjunkande accuracy blir ett förslag, inte en åtgärd."""

    def _report_with_runs(self, accuracies):
        coworker = self._coworker(f'Kaizen eval {len(accuracies)}')
        report = self.env['ai.kaizen.report'].create({
            'coworker_id': coworker.id,
            'week_start': '2026-01-05',
            'week_end': '2026-01-11',
        })
        for accuracy in accuracies:
            self.Run.create({
                'coworker_id': coworker.id,
                'accuracy': accuracy,
                'total_cases': 10,
            })
        report.eval_accuracy_trend = accuracies[-1]
        return coworker, report

    def _eval_findings(self, coworker, report):
        data = {
            'session_count': 1, 'error_count': 0, 'cost_trend': 0.0,
            'feedback_count': 0, 'total_sys_tokens': 0,
        }
        return [
            f for f in report._analyze_findings(coworker, report, data)
            if f.get('category') == 'performance'
        ]

    def test_finding_when_accuracy_drops(self):
        """6.1: lägre accuracy än föregående run ⇒ performance-fynd."""
        coworker, report = self._report_with_runs([0.9, 0.6])
        findings = self._eval_findings(coworker, report)
        self.assertEqual(len(findings), 1)
        evidence = findings[0]['evidence']
        self.assertIn('0.9000', evidence)
        self.assertIn('0.6000', evidence)

    def test_no_finding_when_accuracy_rises(self):
        """6.2: stigande accuracy ⇒ inget fynd."""
        coworker, report = self._report_with_runs([0.6, 0.9])
        self.assertEqual(self._eval_findings(coworker, report), [])

    def test_no_finding_with_fewer_than_two_runs(self):
        """6.2: ett ensamt run säger inget om riktningen."""
        coworker, report = self._report_with_runs([0.4])
        self.assertEqual(self._eval_findings(coworker, report), [])

    def test_finding_is_pending_and_not_applied(self):
        """6.3: fyndet är ett förslag — ingen skill skrivs utan människa."""
        coworker, report = self._report_with_runs([0.9, 0.6])
        finding = self.env['ai.kaizen.finding'].create({
            'report_id': report.id,
            **self._eval_findings(coworker, report)[0],
        })
        self.assertEqual(finding.status, 'pending')
        self.assertFalse(finding.skill_suggestion)
        # `action_apply_to_skill` nekar allt som inte är godkänt — det är
        # HITL-grinden, inte en bugg. Fyndet förblir `pending`.
        with self.assertRaises(ValueError):
            finding.action_apply_to_skill()
        self.assertEqual(finding.status, 'pending')
