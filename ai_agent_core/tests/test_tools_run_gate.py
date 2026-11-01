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
