# -*- coding: utf-8 -*-
"""Rollnormalisering på Pi-vägen (session-observability).

Pi:s egen session kallar verktygsresultatet `toolResult`; OpenAI-formatet (och
Pi:s egen openai-completions-adapter) kallar det `tool`. Skickades Pi:s
vokabulär rakt igenom blev `Role('toolResult')` ett `ValueError` som
`_to_message_list` svalde med `continue` — verktygsresultatet nådde aldrig
LLM:en, som tappade kopplingen mellan `tool_calls` och deras svar. I
persisteringen föll posten i stället tillbaka på `user`, så sessionen visade
verktygets utdata som om användaren skrivit den.

Dessa tester låser att:

  1. `normalize_role_name` mappar Pi:s vokabulär till `tool`.
  2. `_to_message_list` (till providern) ger en `tool`-post med `tool_call_id`.
  3. `_persist_pi_messages` (till databasen) ger en `role='tool'`-rad.
  4. Okänd roll hoppas över i meddelandekonverteringen men bevaras som `user`
     i persisteringen — turen får inte tappas.
"""

from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestPiRoleNormalization(TransactionCase):
    """`toolResult` → `tool` i båda riktningarna."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Session = cls.env['ai.coworker.session']

    # ── 1. Den delade hjälparen ────────────────────────────────────────

    def test_normalize_role_name_maps_pi_vocabulary(self):
        from odoo.addons.ai_agent_core.core.provider import (
            normalize_role_name)
        self.assertEqual(normalize_role_name('toolResult'), 'tool')
        self.assertEqual(normalize_role_name('toolresult'), 'tool')
        self.assertEqual(normalize_role_name('tool_result'), 'tool')
        self.assertEqual(normalize_role_name(' ToolResult '), 'tool')
        # Kända roller och okända namn lämnas orörda
        self.assertEqual(normalize_role_name('assistant'), 'assistant')
        self.assertEqual(normalize_role_name('bashExecution'), 'bashExecution')
        self.assertEqual(normalize_role_name(''), '')
        self.assertIsNone(normalize_role_name(None))

    # ── 2. Till providern ──────────────────────────────────────────────

    def test_to_message_list_keeps_toolresult_as_tool(self):
        """Verktygsresultatet når LLM:en som en `tool`-post."""
        from odoo.addons.ai_agent_core.controllers.stream import (
            _to_message_list)
        msgs = _to_message_list([
            {'role': 'user', 'content': 'kör pwd'},
            {'role': 'assistant', 'content': '', 'tool_calls': [
                {'id': 'call_xyz', 'type': 'function',
                 'function': {'name': 'bash', 'arguments': '{}'}}]},
            {'role': 'toolResult', 'tool_call_id': 'call_xyz',
             'name': 'bash',
             'content': [{'type': 'text', 'text': '/home/waland\n'}]},
        ])
        self.assertEqual(len(msgs), 3, 'toolResult får inte kastas bort')
        self.assertEqual([m.role.value for m in msgs],
                         ['user', 'assistant', 'tool'])
        self.assertEqual(msgs[-1].tool_call_id, 'call_xyz')
        self.assertEqual(msgs[-1].content, '/home/waland\n')

    def test_to_message_list_skips_unknown_role(self):
        """En okänd roll hoppas över — men de kända posterna bevaras."""
        from odoo.addons.ai_agent_core.controllers.stream import (
            _to_message_list)
        msgs = _to_message_list([
            {'role': 'user', 'content': 'hej'},
            {'role': 'bashExecution', 'command': 'pwd', 'output': '/tmp'},
            {'role': 'assistant', 'content': 'svar'},
        ])
        self.assertEqual([m.role.value for m in msgs], ['user', 'assistant'])

    # ── 3. Till databasen ──────────────────────────────────────────────

    def test_persist_toolresult_becomes_tool_line(self):
        """`toolResult` sparas som verktygsrad, inte som `user`."""
        session = self.Session.create({
            'name': 'toolresult-role', 'init_type': 'openai_api'})
        self.Session._persist_pi_messages(self.env, session, [
            {'role': 'assistant', 'content': '', 'tool_calls': [
                {'id': 'call_xyz', 'type': 'function',
                 'function': {'name': 'bash', 'arguments': '{}'}}]},
            {'role': 'toolResult', 'toolCallId': 'call_xyz',
             'toolName': 'bash',
             'content': [{'type': 'text', 'text': '/home/waland\n'}]},
        ])
        lines = session.session_line_ids.sorted('sequence')
        roles = [l.role for l in lines]
        self.assertIn('tool', roles, 'toolResult ska bli en tool-rad')
        self.assertNotIn('user', roles,
                         'toolResult får inte förkläs till en användartur')
        tool_line = lines.filtered(lambda l: l.role == 'tool')
        self.assertEqual(len(tool_line), 1)
        self.assertEqual(tool_line.tool_call_id, 'call_xyz')
        self.assertEqual(tool_line.content, '/home/waland\n')

    def test_persist_unknown_role_falls_back_to_user(self):
        """En okänd roll blir `user` — persistensen får inte tappa turen."""
        session = self.Session.create({
            'name': 'unknown-role', 'init_type': 'openai_api'})
        n = self.Session._persist_pi_messages(self.env, session, [
            {'role': 'user', 'content': 'hej'},
            {'role': 'bashExecution', 'command': 'pwd', 'output': '/tmp'},
        ])
        self.assertEqual(n, 2)
        self.assertEqual(
            [l.role for l in session.session_line_ids.sorted('sequence')],
            ['user', 'user'])

    # ── 4. Spåret: tool_call-rader (task 1.6) ───────────────────────────

    def test_trace_tool_calls_writes_one_row_per_call(self):
        """Varje exekverat verktyg blir en `tool_call`-rad."""
        from odoo.addons.ai_agent_core.controllers.stream import (
            _trace_tool_calls)
        session = self.Session.create({
            'name': 'trace-tools', 'init_type': 'openai_api'})
        n = _trace_tool_calls(
            session,
            [{'id': 'call_a', 'name': 'odoo_search',
              'arguments': '{"model": "res.partner"}'},
             {'id': 'call_b', 'name': 'odoo_read',
              'arguments': '{"model": "res.partner", "ids": [1]}'}],
            [{'name': 'odoo_search', 'result': '[]', 'is_error': False,
              '_duration_ms': 12, '_args': {'model': 'res.partner'}},
             {'name': 'odoo_read', 'result': 'boom', 'is_error': True,
              '_duration_ms': 3, '_args': {}}],
            executor='odoo')
        self.assertEqual(n, 2)
        rows = session.env['ai.coworker.session.tool_call'].search(
            [('session_id', '=', session.id)], order='id')
        self.assertEqual(len(rows), 2)
        self.assertEqual([r.executor for r in rows], ['odoo', 'odoo'])
        self.assertEqual(rows[0].tool_name, 'odoo_search')
        self.assertEqual(rows[0].duration_ms, 12)
        self.assertFalse(rows[0].is_error)
        self.assertEqual(rows[1].result_preview, 'boom')
        self.assertTrue(rows[1].is_error)

    def test_trace_tool_calls_dedupes_on_call_id(self):
        """Samma `tool_call_id` två gånger ger exakt en rad."""
        from odoo.addons.ai_agent_core.controllers.stream import (
            _trace_tool_calls)
        session = self.Session.create({
            'name': 'trace-dedup', 'init_type': 'openai_api'})
        call = [{'id': 'call_dup', 'name': 'bash', 'arguments': '{}'}]
        res = [{'name': 'bash', 'result': 'ok', 'is_error': False}]
        _trace_tool_calls(session, call, res, executor='pi_local')
        _trace_tool_calls(session, call, res, executor='pi_local')
        rows = session.env['ai.coworker.session.tool_call'].search(
            [('session_id', '=', session.id)])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows.executor, 'pi_local')

    def test_trace_tool_calls_keeps_parent_for_subagent_tree(self):
        """`parent_tool_call_id` bevaras så subagent-trädet kan rekonstrueras."""
        from odoo.addons.ai_agent_core.controllers.stream import (
            _trace_tool_calls)
        session = self.Session.create({
            'name': 'trace-parent', 'init_type': 'openai_api'})
        _trace_tool_calls(
            session,
            [{'id': 'call_child', 'name': 'read',
              'parent_tool_call_id': 'call_parent', 'arguments': '{}'}],
            [{'name': 'read', 'result': 'x', 'is_error': False}],
            executor='pi_local')
        row = session.env['ai.coworker.session.tool_call'].search(
            [('session_id', '=', session.id)])
        self.assertEqual(row.parent_tool_call_id, 'call_parent')

    # ── 5. pi_trace (task 4.1–4.3) ──────────────────────────────────────

    def test_pi_trace_tools_become_pi_local_rows(self):
        """`pi_trace.tools[]` skrivs som `executor='pi_local'`."""
        from odoo.addons.ai_agent_core.controllers.stream import (
            _trace_tool_calls)
        session = self.Session.create({
            'name': 'pi-trace', 'init_type': 'openai_api'})
        trace = {'turn': 3, 'started_at': '2026-11-09 09:00:00', 'tools': [
            {'id': 'call_1', 'name': 'bash', 'executor': 'pi_local',
             'args': {'command': 'pwd'}, 'result_preview': '/home/waland',
             'is_error': False, 'duration_ms': 42},
        ]}
        _trace_tool_calls(session, trace['tools'], trace['tools'],
                          executor='pi_local')
        row = session.env['ai.coworker.session.tool_call'].search(
            [('session_id', '=', session.id)])
        self.assertEqual(len(row), 1)
        self.assertEqual(row.executor, 'pi_local')
        self.assertEqual(row.duration_ms, 42)
        self.assertEqual(row.tool_call_id, 'call_1')

    def test_no_pi_trace_writes_no_tool_rows(self):
        """En tur utan trace ger inga tool_call-rader — men sessionen finns."""
        from odoo.addons.ai_agent_core.controllers.stream import (
            _trace_tool_calls)
        session = self.Session.create({
            'name': 'no-trace', 'init_type': 'openai_api'})
        n = _trace_tool_calls(session, [], [], executor='pi_local')
        self.assertEqual(n, 0)
        self.assertEqual(
            session.env['ai.coworker.session.tool_call'].search_count(
                [('session_id', '=', session.id)]), 0)

    def test_agent_run_records_skills_and_kind(self):
        """`agent_run` bär kind, agent, skills och modell."""
        session = self.Session.create({
            'name': 'agent-run', 'init_type': 'openai_api'})
        skill = self.env['ai.skill'].create({
            'name': 'trace-skill', 'description': 'x'})
        agent = self.env['ai.agent'].create({
            'name': 'trace-agent', 'description': 'y'})
        run = self.env['ai.coworker.session.agent_run'].create({
            'session_id': session.id,
            'kind': 'pi_generation',
            'agent_id': agent.id,
            'skill_ids': [(6, 0, [skill.id])],
            'model_requested': 'Allmän assistent',
            'model_served': 'deepseek-v3',
            'turn_index': 2,
            'tokens_in': 100,
            'tokens_out': 50,
            'finish_reason': 'stop',
        })
        self.assertEqual(run.kind, 'pi_generation')
        self.assertEqual(run.skill_ids, skill)
        self.assertEqual(run.model_requested, 'Allmän assistent')
        self.assertEqual(run.model_served, 'deepseek-v3')
        self.assertEqual(run.tokens_in, 100)
