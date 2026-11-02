# -*- coding: utf-8 -*-
"""HITL-grinden på /ai/v1/tools/run (ai-agent-core-fault-remediation).

Bakgrund: `/ai/v1/tools/run` är den mest trafikerade vägen — det är den
Pi-extensionen använder för att köra Odoo-verktyg. Den byggde en
`ToolRegistry` och anropade `tool.execute()` DIREKT, utan
`PermissionEngine.evaluate()`. Följden:

  * `odoo_call_method` (RiskClass.EXEC) och `odoo_unlink`
    (RiskClass.EXTERNAL) — de två HÅRDA STOPEN i
    `core/permission.py:303` — kunde köras utan mänskligt godkännande.
  * `approvals`-specen säger "Hard stops SHALL apply regardless of trust
    step and runtime". Så var det inte.

Testerna bevakar tre saker:
  1. Ett hårt stopp NEKAS på denna väg (3.1/3.3).
  2. Nekandet formateras som ett verktygsfel som Pi-klienten kan läsa.
  3. Ett läsande verktyg passerar fortfarande (3.4) — grinden får inte
     bli ett prestandahinder för normal drift.

HTTP-testerna observerar bara svaret (samma princip som
test_openai_api_compaction: HttpCase kör i egen cursor).
"""

import json

from odoo.tests.common import TransactionCase, tagged

from odoo.addons.ai_agent_core.core.permission import (
    Decision,
    PermissionEngine,
    PermissionMode,
    RiskClass,
    classify,
)


@tagged('ai_core', 'post_install', '-at_install')
class TestHardStopsClassify(TransactionCase):
    """Klassningen är grunden — hårda stopp måste klassas som icke-läsande."""

    def test_odoo_call_method_is_exec(self):
        self.assertEqual(classify('odoo_call_method', 'read_only'), RiskClass.EXEC)

    def test_odoo_unlink_is_external(self):
        self.assertEqual(
            classify('odoo_unlink', 'read_only'), RiskClass.EXTERNAL)

    def test_hard_stop_denied_in_auto_mode(self):
        """AUTO får aldrig köra hårda stopp (automation utan människa)."""
        engine = PermissionEngine(mode=PermissionMode.AUTO)
        decision = engine.evaluate('odoo_call_method', {'model': 'res.partner'})
        self.assertFalse(decision.allowed)
        self.assertIn('hårt stopp', decision.reason)

    def test_hard_stop_needs_user_in_interactive(self):
        engine = PermissionEngine(mode=PermissionMode.INTERACTIVE)
        decision = engine.evaluate('odoo_call_method', {'model': 'res.partner'})
        self.assertTrue(decision.needs_user)
        self.assertIn('hårt stopp', decision.reason)

    def test_read_tool_passes_without_user(self):
        """Läsande verktyg ska passera tyst — annars blir grinden ett hinder."""
        engine = PermissionEngine(mode=PermissionMode.INTERACTIVE)
        decision = engine.evaluate('odoo_search', {'model': 'res.partner'})
        self.assertTrue(decision.allowed)
        self.assertFalse(decision.needs_user)


@tagged('ai_core', 'post_install', '-at_install')
class TestToolsRunGate(TransactionCase):
    """Grinden på /ai/v1/tools/run — beteendet som saknades.

    Vi testar beslutsfunktionen som rutten nu använder, inte HTTP-lagret:
    HTTP-lagret kräver en riktig API-nyckel, och det som ska bevakas är
    OM `evaluate()` anropas och HUR nekandet formateras.
    """

    def _gate(self, tool_name, arguments=None, mode=PermissionMode.INTERACTIVE):
        """Samma beslut som rutten fattar (stream.py tools_run)."""
        engine = PermissionEngine(mode=mode)
        return engine.evaluate(tool_name, arguments or {})

    def _format_denial(self, name, decision):
        """Ruttens formatering av ett nekat anrop (3.3)."""
        return {
            'name': name,
            'result': f'Permission denied: {decision.reason}',
            'is_error': True,
        }

    def test_call_method_is_denied_not_executed(self):
        """3.1/3.3: odoo_call_method nekas — anropet får inte exekveras."""
        decision = self._gate('odoo_call_method', {'model': 'res.partner',
                                                   'method': 'unlink'})
        self.assertTrue(decision.needs_user,
                        'odoo_call_method är ett hårt stopp — måste kräva människa')

        denial = self._format_denial('odoo_call_method', decision)
        self.assertEqual(denial['name'], 'odoo_call_method')
        self.assertTrue(denial['is_error'],
                        'Pi-klienten läser is_error — nekandet måste sätta den')
        self.assertIn('Permission denied', denial['result'])

    def test_unlink_is_denied(self):
        decision = self._gate('odoo_unlink', {'model': 'res.partner',
                                              'ids': [1]})
        self.assertTrue(decision.needs_user)

    def test_search_passes_the_gate(self):
        """3.4: läsande verktyg passerar utan godkännande."""
        decision = self._gate('odoo_search', {'model': 'res.partner',
                                              'domain': []})
        self.assertTrue(decision.allowed)
        self.assertFalse(decision.needs_user,
                         'odoo_search får inte kräva godkännande')

    def test_denial_shape_matches_result_contract(self):
        """Nekandet ska ha SAMMA form som ett lyckat resultat.

        Rutten returnerar {'name', 'result', 'is_error'} per verktygsanrop.
        Ett nekat anrop som avvek i form hade tystat Pi-klienten.
        """
        decision = self._gate('odoo_call_method', {})
        denial = self._format_denial('odoo_call_method', decision)
        self.assertEqual(set(denial.keys()), {'name', 'result', 'is_error'})
        self.assertIsInstance(denial['result'], str)
        self.assertIsInstance(denial['is_error'], bool)


@tagged('ai_core', 'post_install', '-at_install')
class TestToolsRunRouteUsesGate(TransactionCase):
    """Strukturell grind: rutten FÅR INTE anropa tool.execute() ogranskat.

    Detta är testet som hade fångat buggen. Ett beteendetest hade bara visat
    att ett specifikt verktyg nekades; detta visar att grinden finns på
    vägen över huvud taget — och att den inte kan tas bort tyst igen.
    """

    def test_tools_run_calls_permission_engine(self):
        import ast
        import inspect
        import textwrap
        from odoo.addons.ai_agent_core.controllers import stream as stream_mod

        src = textwrap.dedent(
            inspect.getsource(stream_mod.AIOpenAIAPI.tools_run))
        tree = ast.parse(src)

        # Hitta _run_one (den inre funktionen som exekverar verktyget).
        run_one = None
        for node in ast.walk(tree):
            if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) \
                    and node.name == '_run_one':
                run_one = node
                break
        self.assertIsNotNone(run_one, '_run_one saknas i tools_run')

        body_src = ast.get_source_segment(src, run_one) or ''

        self.assertIn(
            'evaluate', body_src,
            '/ai/v1/tools/run måste anropa PermissionEngine.evaluate() '
            'innan tool.execute() — annars gäller inte de hårda stoppen')
        # Motorn byggs utanför _run_one (en gång per request) — det som
        # måste synas i _run_one är att den ANVÄNDS.
        self.assertIn(
            '_permissions', body_src,
            '_run_one måste använda den byggda PermissionEngine')

        # Grinden måste ligga FÖRE execute — annars är den verkningslös.
        eval_pos = body_src.find('evaluate')
        exec_pos = body_src.find('tool.execute')
        self.assertNotEqual(exec_pos, -1, 'tool.execute saknas i _run_one')
        self.assertLess(
            eval_pos, exec_pos,
            'evaluate() måste anropas FÖRE tool.execute()')


@tagged('ai_core', 'post_install', '-at_install')
class TestPiApprovalBridge(TransactionCase):
    """Godkännande givet i Pi:s UI får passera /tools/run — men bara rätt saker.

    Bakgrund: /tools/run har ingen godkännandekanal. PermissionEngine säger
    needs_user för skrivande verktyg, grinden nekar fail-closed, och Pi får
    ett fel den inte kan göra något åt. Pi-extensionen fångar nekandet,
    frågar användaren i sin egen dialog ("bara denna gång" / "hela
    sessionen") och skickar om anropet med `approved_by_user: true`.

    Det som bevakas:
      1. Ett skrivande verktyg med godkännande passerar grinden.
      2. Utan godkännande nekas det fortfarande (fail-closed är kvar).
      3. HÅRDA STOPP nekas ÄVEN med godkännande — Pi:s dialog visar bara en
         sammanfattning och får inte bli en genväg runt dem.
    """

    def _gate(self, tool_name, approved, mode=PermissionMode.INTERACTIVE):
        """Samma beslut som rutten fattar efter ändringen (stream.py)."""
        from odoo.addons.ai_agent_core.core.permission import (
            _ODOO_EXEC_TOOLS, _ODOO_EXTERNAL_TOOLS)
        engine = PermissionEngine(mode=mode)
        decision = engine.evaluate(tool_name, {})
        is_hard_stop = (
            tool_name in _ODOO_EXEC_TOOLS or tool_name in _ODOO_EXTERNAL_TOOLS)
        needs_user_unmet = (
            decision.needs_user and not (approved and not is_hard_stop))
        return not decision.allowed or needs_user_unmet

    def test_write_passes_with_approval(self):
        """1: odoo_write med godkännande från Pi ska släppas igenom."""
        self.assertFalse(
            self._gate('odoo_write', approved=True),
            'Ett godkänt odoo_write ska passera grinden')

    def test_write_denied_without_approval(self):
        """2: utan godkännande är grinden fortfarande fail-closed."""
        self.assertTrue(
            self._gate('odoo_write', approved=False),
            'odoo_write utan godkännande måste nekas')

    def test_create_passes_with_approval(self):
        self.assertFalse(self._gate('odoo_create', approved=True))

    def test_hard_stop_call_method_denied_even_with_approval(self):
        """3: odoo_call_method är ett hårt stopp — godkännande räcker inte."""
        self.assertTrue(
            self._gate('odoo_call_method', approved=True),
            'odoo_call_method får ALDRIG köras på /tools/run, ens godkänd — '
            'Pi-dialogen visar bara en sammanfattning av anropet')

    def test_hard_stop_unlink_denied_even_with_approval(self):
        self.assertTrue(
            self._gate('odoo_unlink', approved=True),
            'odoo_unlink får ALDRIG köras på /tools/run, ens godkänd')

    def test_read_tool_unaffected(self):
        """Läsande verktyg ska varken kräva eller bry sig om godkännande."""
        self.assertFalse(self._gate('odoo_search', approved=False))
        self.assertFalse(self._gate('odoo_search', approved=True))

    def test_route_reads_approved_by_user(self):
        """Strukturellt: rutten måste läsa flaggan ur bodyn.

        Utan den här kontrollen kan flaggan tas bort tyst och Pi:s
        godkännandedialog blir verkningslös — den skulle fråga, få ja, och
        ändå nekas av Odoo.
        """
        import inspect
        import textwrap
        from odoo.addons.ai_agent_core.controllers import stream as stream_mod

        src = textwrap.dedent(
            inspect.getsource(stream_mod.AIOpenAIAPI.tools_run))
        self.assertIn(
            'approved_by_user', src,
            '/tools/run måste läsa approved_by_user ur bodyn')
        self.assertIn(
            '_is_hard_stop', src,
            'Hårda stopp måste undantas från godkännande-vägen')


@tagged('ai_core', 'post_install', '-at_install')
class TestApprovalChannel(TransactionCase):
    """tools-run-approval-channel 2.1–3.5, 4.2–4.3.

    Kanalen: när grinden säger `needs_user` registreras en begäran på
    sessionens handler i stället för att neka blint. Klienten pollar
    `/ai/interrupt/poll` och svarar på `/ai/interrupt/respond`.
    """

    def _handler(self):
        from odoo.addons.ai_agent_core.core.interrupt import (
            WebUIInterruptHandler)
        return WebUIInterruptHandler('test-approval', env=self.env)

    def _request(self, **kw):
        from odoo.addons.ai_agent_core.core.interrupt import ApprovalRequest
        args = dict(
            request_id='req-1',
            tool_name='odoo_write',
            arg_keys=['partner_id', 'name'],
            risk_class='write',
            reason='write tool — requires user approval',
        )
        args.update(kw)
        return ApprovalRequest(**args)

    # ── 2.1/2.2: registrering och form ──

    def test_request_can_be_registered_and_read(self):
        handler = self._handler()
        req = self._request()
        handler.register_approval(req)
        self.assertEqual(len(handler.get_approvals()), 1)
        self.assertIs(handler.get_approvals()[0], req)

    def test_request_carries_keys_not_values(self):
        """2.2: `arg_keys` är nycklar — argumentvärden får inte läcka."""
        req = self._request()
        payload = req.to_dict()
        self.assertEqual(payload['arg_keys'], ['partner_id', 'name'])
        # Inget värde får finnas i payloaden.
        self.assertNotIn('Acme', json.dumps(payload))
        self.assertEqual(
            set(payload),
            {'type', 'request_id', 'tool_name', 'arg_keys',
             'risk_class', 'reason', 'created'})

    def test_approval_survives_repeated_reads(self):
        """Till skillnad från `_pending` töms inte en begäran vid läsning."""
        handler = self._handler()
        handler.register_approval(self._request())
        self.assertEqual(len(handler.get_approvals()), 1)
        self.assertEqual(len(handler.get_approvals()), 1)

    # ── 3.x: svarskanalen ──

    def test_answer_is_taken_once(self):
        handler = self._handler()
        handler.register_approval(self._request())
        self.assertTrue(handler.answer_approval('req-1', True))
        self.assertEqual(
            handler.take_approval_response('req-1'),
            {'approved': True, 'reason': ''})
        self.assertIsNone(
            handler.take_approval_response('req-1'),
            'svaret ska bara kunna tas en gång')

    def test_answer_removes_the_pending_request(self):
        handler = self._handler()
        handler.register_approval(self._request())
        handler.answer_approval('req-1', False, 'nej')
        self.assertEqual(handler.get_approvals(), [])

    def test_answer_for_unknown_request_is_refused(self):
        handler = self._handler()
        self.assertFalse(handler.answer_approval('finns-inte', True))

    def test_response_shape_is_distinct_from_ask(self):
        """Ett godkännandesvar får inte förväxlas med `ask`-svaret."""
        handler = self._handler()
        handler.register_approval(self._request())
        handler.answer_approval('req-1', True)
        answer = handler.take_approval_response('req-1')
        self.assertIsInstance(answer, dict)
        self.assertIn('approved', answer)
        self.assertNotIn('action', answer)

    # ── 4.2/4.3: loggning utan argumentvärden ──

    def test_approval_log_has_no_argument_values(self):
        """4.2: loggraden namnger nycklarna, aldrig värdena."""
        import inspect
        import textwrap
        from odoo.addons.ai_agent_core.controllers import stream as stream_mod
        src = textwrap.dedent(inspect.getsource(stream_mod._log_approval))
        self.assertIn('arg_keys', src)
        self.assertNotIn('args.items()', src,
                         'loggen får inte skriva ut argumentvärden')
        self.assertNotIn('args)', src.split('sorted(')[-1][:40])

    def test_denial_log_exists(self):
        import inspect
        from odoo.addons.ai_agent_core.controllers import stream as stream_mod
        self.assertTrue(callable(stream_mod._log_denial))
        self.assertIn('NEKAT', inspect.getsource(stream_mod._log_denial))

    # ── 5.2: en needs_user-gren måste försöka registrera ──

    def test_needs_user_tries_the_channel_first(self):
        """5.2: grenen får inte neka utan att först försöka fråga."""
        import inspect
        import textwrap
        from odoo.addons.ai_agent_core.controllers import stream as stream_mod
        src = textwrap.dedent(
            inspect.getsource(stream_mod.AIOpenAIAPI.tools_run))
        self.assertIn('_approval_handler_for', src,
                      'needs_user-grenen måste slå upp en handler')
        self.assertIn('_register_approval', src,
                      'needs_user-grenen måste registrera en begäran')
        self.assertIn('_wait_for_approval', src,
                      'needs_user-grenen måste vänta på svaret')

    def test_hard_stop_never_reaches_the_channel(self):
        """Hårda stopp ska nekas utan att fråga — även med kanal."""
        import inspect
        import textwrap
        from odoo.addons.ai_agent_core.controllers import stream as stream_mod
        src = textwrap.dedent(
            inspect.getsource(stream_mod.AIOpenAIAPI.tools_run))
        self.assertIn(
            'not _is_hard_stop', src,
            'kanal-grenen måste undanta hårda stopp')


@tagged('ai_core', 'post_install', '-at_install')
class TestApprovalChannelAuth(TransactionCase):
    """4.1 — ett godkännande är en behörighetshandling."""

    def test_respond_requires_api_key_for_approvals(self):
        """Ett godkännandesvar får inte lämnas utan API-nyckel."""
        import inspect
        import textwrap
        from odoo.addons.ai_agent_core.controllers import stream as stream_mod
        src = textwrap.dedent(
            inspect.getsource(stream_mod.AIStreamController.interrupt_respond))
        # Kontrollen måste ligga INUTI request_id-grenen.
        idx_approval = src.find("response.get('request_id')")
        idx_auth = src.find('_check_api_key', idx_approval)
        self.assertGreater(
            idx_auth, idx_approval,
            'godkännandegrenen måste pröva API-nyckeln innan svaret tas emot')

    def test_plain_ask_response_is_not_gated(self):
        """Det vanliga `ask`-svaret lämnas orört (web-UI:ts egen kanal)."""
        import inspect
        import textwrap
        from odoo.addons.ai_agent_core.controllers import stream as stream_mod
        src = textwrap.dedent(
            inspect.getsource(stream_mod.AIStreamController.interrupt_respond))
        # set_response ska nås utan API-nyckel — den ligger efter grenen.
        idx_set = src.find('handler.set_response(response)')
        idx_auth = src.find('_check_api_key')
        self.assertGreater(idx_set, idx_auth)


@tagged('ai_core', 'post_install', '-at_install')
class TestPiFlagLeavesATrace(TransactionCase):
    """Path B: `approved_by_user` finns kvar men lämnar nu ett spår."""

    def test_flag_logs_the_approval(self):
        """Flaggan ska loggas — annars finns godkännandet bara i Pi."""
        import inspect
        import textwrap
        from odoo.addons.ai_agent_core.controllers import stream as stream_mod
        src = textwrap.dedent(
            inspect.getsource(stream_mod.AIOpenAIAPI.tools_run))
        self.assertIn(
            '_log_approval(sess, name', src,
            'godkännande via flaggan måste lämna ett spår på sessionen')

    def test_flag_still_exempts_hard_stops(self):
        """Hårda stopp nekas fortfarande — flaggan är inget undantag."""
        import inspect
        import textwrap
        from odoo.addons.ai_agent_core.controllers import stream as stream_mod
        src = textwrap.dedent(
            inspect.getsource(stream_mod.AIOpenAIAPI.tools_run))
        self.assertIn('approved_by_user and not _is_hard_stop', src)
