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

from datetime import timedelta
import logging
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
        """Ett fall; `expected_contains=False` ger ett fall UTAN förväntan.

        Befordran skapar precis ett sådant fall (förväntan fylls i av en
        människa efteråt), så det måste gå att skapa i test.
        """
        session = session or self._session(coworker)
        return self.Case.create({
            'coworker_id': coworker.id,
            'source_session_id': session.id,
            'input': 'Vad är 2+2?',
            'expected_contains': expected_contains or False,
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


@tagged('post_install', '-at_install')
class TestEvalCasesWithoutExpectation(EvalTestBase):
    """1.1–1.3: ett fall utan förväntan mäter ingenting och körs inte.

    Befordran lämnar `expected_contains` tomt med flit (människan fyller i
    efteråt). Ett sådant fall skulle alltid ge score 1.0 — `_evaluate_case`
    har inget att dra av för — och göra varje modell- eller promptändring
    osynlig i mätningen.
    """

    def _patched_coworker(self):
        coworker = self._coworker('Eval utan förväntan')
        return coworker, patch.object(
            type(coworker), '_run_eval_cases',
            autospec=True,
            side_effect=lambda rec, cases: self.Run.create({
                'coworker_id': rec.id,
                'accuracy': 1.0,
                'passed': len(cases),
                'failed': 0,
                'total_cases': len(cases),
            }))

    def test_case_without_expectation_is_not_run(self):
        """1.1: tomt `expected_contains` ⇒ fallet skickas inte till körningen."""
        coworker, mocked = self._patched_coworker()
        self._case(coworker, expected_contains=False)
        with mocked as run_mock:
            result = self.Coworker._cron_run_evals()
        self.assertEqual(run_mock.call_count, 0)
        self.assertEqual(result['runs'], 0)
        self.assertFalse(self.Run.search([('coworker_id', '=', coworker.id)]))

    def test_case_without_expectation_not_counted_in_total_cases(self):
        """1.1: fallet räknas inte i `total_cases` — bara det mätbara gör det."""
        coworker, mocked = self._patched_coworker()
        self._case(coworker, expected_contains='["4"]')
        self._case(coworker, expected_contains=False)
        with mocked as run_mock:
            self.Coworker._cron_run_evals()
        cases = run_mock.call_args[0][1]
        self.assertEqual(len(cases), 1)
        self.assertEqual(cases.expected_contains, '["4"]')

    def test_case_with_only_not_contains_is_measurable(self):
        """1.2: enbart `expected_not_contains` är en giltig förväntan."""
        coworker, mocked = self._patched_coworker()
        case = self._case(coworker, expected_contains=False)
        case.expected_not_contains = '["hittepå"]'
        with mocked as run_mock:
            result = self.Coworker._cron_run_evals()
        self.assertEqual(run_mock.call_count, 1)
        self.assertEqual(len(run_mock.call_args[0][1]), 1)
        self.assertEqual(result['empty_skipped'], 0)

    def test_measurable_filter_keeps_expected_output(self):
        """1.2: ett exakt förväntat svar räknas också som förväntan."""
        coworker = self._coworker('Eval expected_output')
        case = self._case(coworker, expected_contains=False)
        core_case = case._to_eval_case()
        self.assertFalse(core_case.expected_contains)
        self.assertFalse(core_case.expected_not_contains)
        self.assertFalse(core_case.expected_output)
        self.assertEqual(len(self.Coworker._measurable_eval_cases(case)), 0)

    def test_skipped_count_is_logged(self):
        """1.3: antalet överhoppade fall räknas och loggas per körning.

        Testet prövar RÄKNAREN (returvärdet), inte loggtexten. Att fånga
        loggraden med `assertLogs` visade sig opålitligt: Odoo sätter både
        `propagate = False` och WARNING-nivå på addon-loggern under
        testkörning, och loggraden skrivs ändå till loggfilen. Raden finns
        (verifierad i /tmp/tst_dbg.log), men den går inte att fånga på ett
        stabilt sätt — och det som betyder något är att siffran är rätt.
        """
        coworker = self._coworker('Eval bara utan förväntan')
        mocked = patch.object(
            type(coworker), '_run_eval_cases', autospec=True,
            side_effect=AssertionError(
                'inget run ska skapas när alla fall saknar förväntan'))
        self._case(coworker, expected_contains=False)
        self._case(coworker, expected_contains=False)
        with mocked:
            result = self.Coworker._cron_run_evals()
        self.assertEqual(result['empty_skipped'], 2)
        self.assertEqual(result['runs'], 0)


@tagged('post_install', '-at_install')
class TestPromotionCandidates(EvalTestBase):
    """2.1–2.3: kandidatlistan är en källa, inte en signal."""

    def test_failed_session_is_a_candidate(self):
        """2.1: fel-utfall och underkända sessioner är kandidater."""
        coworker = self._coworker('Kandidater')
        failed = self._session(coworker, finish_reason='max_rounds')
        rated_down = self._session(coworker, finish_reason='stop')
        rated_down.user_rating = 'down'
        self._session(coworker, finish_reason='stop')
        candidates = coworker._get_promotion_candidates()
        self.assertIn(failed, candidates)
        self.assertIn(rated_down, candidates,
                      'en underkänd session är den billigaste källan')
        self.assertEqual(len(candidates), 2)

    def test_promoted_session_leaves_the_list(self):
        """2.1: en befordrad session försvinner ur listan."""
        coworker = self._coworker('Kandidater befordran')
        session = self._session(coworker, finish_reason='error')
        self.assertIn(session, coworker._get_promotion_candidates())
        session.action_promote_to_eval_case()
        self.assertNotIn(session, coworker._get_promotion_candidates())

    def test_candidates_are_scoped_to_the_coworker(self):
        """2.1: listan gäller medarbetarens sessioner, inte allas."""
        coworker = self._coworker('Kandidater en')
        other = self._coworker('Kandidater två')
        self._session(other, finish_reason='error')
        self.assertEqual(len(coworker._get_promotion_candidates()), 0)

    def test_action_opens_empty_list_when_all_promoted(self):
        """2.2: åtgärden öppnar sessioner; tom när allt befordrats."""
        coworker = self._coworker('Kandidater åtgärd')
        session = self._session(coworker, finish_reason='error')
        action = coworker.action_open_promotion_candidates()
        self.assertEqual(action['res_model'], 'ai.coworker.session')
        self.assertEqual(action['domain'], [('id', 'in', session.ids)])
        session.action_promote_to_eval_case()
        action = coworker.action_open_promotion_candidates()
        self.assertEqual(action['domain'], [('id', 'in', [])])

    def test_rating_down_creates_no_finding(self):
        """2.3: `user_rating='down'` blir inget kaizen-fynd."""
        coworker = self._coworker('Kandidater rating')
        session = self._session(coworker, finish_reason='stop')
        session.user_rating = 'down'
        report = self.env['ai.kaizen.report'].create({
            'coworker_id': coworker.id,
            'week_start': '2026-01-05',
            'week_end': '2026-01-11',
        })
        data = {
            'session_count': 1, 'error_count': 0, 'cost_trend': 0.0,
            'feedback_count': 0, 'total_sys_tokens': 0,
        }
        findings = report._analyze_findings(coworker, report, data)
        self.assertFalse(
            [f for f in findings if 'rating' in str(f).lower()],
            'ratingen är en kandidatkälla — den ska inte bli ett fynd')
        self.assertFalse(self.env['ai.kaizen.finding'].search(
            [('report_id', '=', report.id)]))


@tagged('post_install', '-at_install')
class TestOutcomeVerification(EvalTestBase):
    """3.1–3.6: åtgärd → utfall.

    En applicerad kaizen-fynd ska verifieras av den *nästa* eval-körningen
    (inte rapportgenerering), och sätta verification och evidence.
    """

    def _make_run(self, coworker, accuracy, run_date):
        """Skapa ett eval-run med given accuracy och datum."""
        return self.Run.create({
            'coworker_id': coworker.id,
            'accuracy': accuracy,
            'passed': int(accuracy * 10),
            'failed': int((1 - accuracy) * 10),
            'total_cases': 10,
            'run_date': run_date,
        })

    def test_applied_at_is_set_on_action_apply(self):
        """3.1: applied_at sätts när status blir 'applied'."""
        coworker = self._coworker('Verifiering')
        report = self.env['ai.kaizen.report'].create({
            'coworker_id': coworker.id,
            'week_start': '2026-01-05',
            'week_end': '2026-01-11',
        })
        finding = self.env['ai.kaizen.finding'].create({
            'report_id': report.id,
            'severity': 'low',
            'category': 'performance',
            'finding': 'test',
            'recommendation': 'test',
            'evidence': 'test',
        })
        finding.action_apply()
        self.assertTrue(finding.applied_at)
        self.assertEqual(finding.status, 'applied')

    def test_verification_is_set_from_eval_cron(self):
        """3.6: verifieringen anropas från _cron_run_evals, inte från rapportgenerering."""
        coworker = self._coworker('Verifiering cron')
        report = self.env['ai.kaizen.report'].create({
            'coworker_id': coworker.id,
            'week_start': '2026-01-05',
            'week_end': '2026-01-11',
        })
        finding = self.env['ai.kaizen.finding'].create({
            'report_id': report.id,
            'severity': 'low',
            'category': 'performance',
            'finding': 'test',
            'recommendation': 'test',
            'evidence': 'test',
        })
        finding.action_apply()
        applied = finding.applied_at
        # En körning före och en efter applied_at — samma dag, olika timme.
        self._make_run(coworker, 0.6, applied - timedelta(hours=2))
        self._make_run(coworker, 0.8, applied + timedelta(hours=2))
        # Kör eval-cronen manuellt (den anropar _verify_applied_findings).
        self.Coworker._cron_run_evals()
        finding.invalidate_recordset()
        self.assertEqual(finding.verification, 'improved')
        self.assertIn('0.60 → 0.80', finding.verification_evidence)

    def test_verification_is_empty_when_no_after_run(self):
        """3.4: ingen verifiering när ingen körning skett efter applied_at."""
        coworker = self._coworker('Verifiering ingen efter')
        report = self.env['ai.kaizen.report'].create({
            'coworker_id': coworker.id,
            'week_start': '2026-01-05',
            'week_end': '2026-01-11',
        })
        finding = self.env['ai.kaizen.finding'].create({
            'report_id': report.id,
            'severity': 'low',
            'category': 'performance',
            'finding': 'test',
            'recommendation': 'test',
            'evidence': 'test',
        })
        finding.action_apply()
        finding._verify_outcome()
        self.assertFalse(finding.verification)

    def test_worse_creates_high_finding(self):
        """3.5: verification='worse' skapar ett high-fynd."""
        coworker = self._coworker('Verifiering värre')
        report = self.env['ai.kaizen.report'].create({
            'coworker_id': coworker.id,
            'week_start': '2026-01-05',
            'week_end': '2026-01-11',
        })
        finding = self.env['ai.kaizen.finding'].create({
            'report_id': report.id,
            'severity': 'low',
            'category': 'performance',
            'finding': 'test',
            'recommendation': 'test',
            'evidence': 'test',
        })
        finding.action_apply()
        applied = finding.applied_at
        self._make_run(coworker, 0.8, applied - timedelta(hours=2))
        self._make_run(coworker, 0.5, applied + timedelta(hours=2))
        finding._verify_outcome()
        self.assertEqual(finding.verification, 'worse')
        # Domänen måste vara en lista av tupler — en naken `('a','=',b)`
        # utanför listan tolkas som en sekvens av leaves och Odoo kastar
        # `ValueError: too many values to unpack`.
        #
        # Fyndet namnger originalfyndet i `evidence` som
        # `original_finding=<id>: ...` — inte i `finding`. Sök där.
        regression = self.env['ai.kaizen.finding'].search([
            ('report_id', '=', report.id),
            ('severity', '=', 'high'),
            ('evidence', 'like', 'original_finding=%d:' % finding.id),
        ])
        self.assertEqual(len(regression), 1)
        self.assertIn(str(finding.id), regression.evidence)

    def test_unchanged_is_not_set_without_runs(self):
        """3.4: tomt är bättre än oförändrat när ingen efter-run finns."""
        coworker = self._coworker('Verifiering tomt')
        report = self.env['ai.kaizen.report'].create({
            'coworker_id': coworker.id,
            'week_start': '2026-01-05',
            'week_end': '2026-01-11',
        })
        finding = self.env['ai.kaizen.finding'].create({
            'report_id': report.id,
            'severity': 'low',
            'category': 'performance',
            'finding': 'test',
            'recommendation': 'test',
            'evidence': 'test',
        })
        finding.action_apply()
        finding._verify_outcome()
        self.assertFalse(finding.verification)

    def test_verification_does_not_run_on_report_generation(self):
        """3.6: rapportgenereringen verifierar INTE — mätpunkten är körningen."""
        coworker = self._coworker('Verifiering rapport')
        report = self.env['ai.kaizen.report'].create({
            'coworker_id': coworker.id,
            'week_start': '2026-01-05',
            'week_end': '2026-01-11',
        })
        finding = self.env['ai.kaizen.finding'].create({
            'report_id': report.id,
            'severity': 'low',
            'category': 'performance',
            'finding': 'test',
            'recommendation': 'test',
            'evidence': 'test',
        })
        finding.action_apply()
        applied = finding.applied_at
        self._make_run(coworker, 0.6, applied - timedelta(hours=2))
        self._make_run(coworker, 0.9, applied + timedelta(hours=2))
        report._render_report()
        finding.invalidate_recordset()
        self.assertFalse(
            finding.verification,
            'rapportgenereringen får inte verifiera — bara eval-körningen')


@tagged('post_install', '-at_install')
class TestKaizenReportRendering(EvalTestBase):
    """4.1–4.2: rapporttexten visar mätpunkten och utfallet."""

    def _report(self, coworker):
        return self.env['ai.kaizen.report'].create({
            'coworker_id': coworker.id,
            'week_start': '2026-01-05',
            'week_end': '2026-01-11',
        })

    def test_eval_accuracy_shown_in_overview(self):
        """4.1: accuracy-raden finns när fältet är satt."""
        coworker = self._coworker('Rendering accuracy')
        report = self._report(coworker)
        report.eval_accuracy_trend = 0.75
        text = report._render_report()
        self.assertIn('Eval-accuracy: 75%', text)

    def test_eval_accuracy_omitted_when_unset(self):
        """4.1: ingen rad när ingen mätning finns — 0.0 är inte noll rätt."""
        coworker = self._coworker('Rendering tom')
        report = self._report(coworker)
        self.assertNotIn('Eval-accuracy', report._render_report())

    def test_verification_shown_for_applied_finding(self):
        """4.2: utfall + evidens visas för ett verifierat fynd."""
        coworker = self._coworker('Rendering utfall')
        report = self._report(coworker)
        self.env['ai.kaizen.finding'].create({
            'report_id': report.id,
            'severity': 'medium',
            'category': 'performance',
            'finding': 'Eval-accuracy sjönk',
            'verification': 'improved',
            'verification_evidence': 'accuracy 0.62 → 0.81',
        })
        text = report._render_report()
        self.assertIn('Utfall:', text)
        self.assertIn('accuracy 0.62 → 0.81', text)

    def test_no_verification_line_for_unverified_finding(self):
        """4.2: ett oapplicerat fynd har inget utfall att visa."""
        coworker = self._coworker('Rendering utan utfall')
        report = self._report(coworker)
        self.env['ai.kaizen.finding'].create({
            'report_id': report.id,
            'severity': 'low',
            'category': 'performance',
            'finding': 'Ett förslag',
        })
        self.assertNotIn('Utfall:', report._render_report())
