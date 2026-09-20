# -*- coding: utf-8 -*-
"""Tester för web-ui-stream-turn-persistens.

Verifierar att en web_ui-streamtur alltid efterlämnar ett assistantsvar
(även när klienten inte POST:ar), att max_rounds-avslutets fel inte sväljs
tyst, att klientens svarspost är idempotent, och att efter-strömmens
uppstädning är tidsbegränsad.

Bakgrund: session 20286 (2026-09-14) fick 18 tool-rader men noll
assistant-rader — turen tappades och tråden såg tom ut.
"""

import asyncio
from unittest.mock import MagicMock

from odoo.tests import common, tagged
from odoo.addons.ai_agent_core.core.provider import TokenEvent, ToolCall


class _ToolLoopingProvider:
    """Provider som alltid begär ett verktyg → loopen når max_rounds.

    `chat_stream` används både för rundorna och för det avslutande
    sammanfattningsanropet. Med `summary_fails=True` kastar det sista
    anropet (efter att rundorna förbrukats).
    """

    def __init__(self, summary_fails=False, summary_text='Slutsats.'):
        self.summary_fails = summary_fails
        self.summary_text = summary_text
        self.calls = 0

    async def chat_stream(self, **kwargs):
        self.calls += 1
        # Om verktyg erbjuds är det en runda (begär alltid ett verktyg);
        # utan verktyg är det sammanfattningsanropet.
        tools = kwargs.get('tools')
        if tools:
            yield TokenEvent(
                type="tool_call_start",
                tool_call=ToolCall(id='c%d' % self.calls,
                                   name='dummy_tool', arguments={}))
            yield TokenEvent(
                type="tool_call_end",
                tool_call=ToolCall(id='c%d' % self.calls,
                                   name='dummy_tool', arguments={}))
            yield TokenEvent(type="done", finish_reason="tool_calls",
                             input_tokens=1, output_tokens=1)
            return
        # Sammanfattningsanropet.
        if self.summary_fails:
            raise RuntimeError("provider nere")
        yield TokenEvent(type="token", token=self.summary_text)
        yield TokenEvent(type="done", finish_reason="stop",
                         input_tokens=2, output_tokens=3)


def _dummy_tools():
    """En riktig ToolRegistry med ett verktyg — så loopen tar verktygsrundan."""
    from odoo.addons.ai_agent_core.core.tools import Tool, ToolRegistry
    reg = ToolRegistry()

    async def _handler(**kwargs):
        return 'ok'

    reg.register(Tool(
        name='dummy_tool', description='test',
        parameters={'type': 'object', 'properties': {}},
        handler=_handler,
    ))
    return reg


def _run_stream(provider, max_rounds=2, tools=None):
    from odoo.addons.ai_agent_core.core.loop import (
        StreamingAgentLoop, AgentConfig)
    loop = StreamingAgentLoop(
        provider=provider,
        tools=tools if tools is not None else _dummy_tools(),
        config=AgentConfig(model='test-model', system_prompt='sys',
                           max_rounds=max_rounds),
    )
    events = []

    async def _collect():
        async for ev in loop.run_stream('hej', history=[]):
            events.append(ev)

    asyncio.new_event_loop().run_until_complete(_collect())
    return events


@tagged('post_install', '-at_install')
class TestMaxRoundsSummary(common.TransactionCase):
    """1.1/1.2: max_rounds-avslutet syns och tystnar inte."""

    def test_summary_failure_is_carried_on_done(self):
        """1.1: fel i avslutande anrop bärs på done och är inte 'stop'."""
        provider = _ToolLoopingProvider(summary_fails=True)
        events = _run_stream(provider, max_rounds=2)
        done = [e for e in events if e.type == 'done']
        self.assertTrue(done, 'done-event saknas')
        last = done[-1]
        self.assertNotEqual(last.finish_reason, 'stop',
                            'avslutsorsaken ska spegla max_rounds')
        self.assertTrue(last.error,
                        'felet i avslutande anrop ska bäras på done')

    def test_summary_success_streams_text_before_done(self):
        """1.2: lyckat avslut strömmar text före done."""
        provider = _ToolLoopingProvider(summary_text='Slutsatsen.')
        events = _run_stream(provider, max_rounds=2)
        types = [e.type for e in events]
        self.assertIn('token', types)
        self.assertEqual(types[-1], 'done')
        text = ''.join(e.token for e in events if e.type == 'token')
        self.assertIn('Slutsatsen.', text)


@tagged('post_install', '-at_install')
class TestStreamAnswerPersist(common.TransactionCase):
    """2.1/2.2/2.3: servern persisterar turens assistantsvar."""

    def _session(self):
        return self.env['ai.coworker.session'].create({
            'name': 'stream-turn-test',
            'status': 'active',
            'init_type': 'web_ui',
        })

    def test_answer_persisted_without_client_post(self):
        """2.1: assistant-rad skrivs server-side utan klient-POST."""
        from odoo.addons.ai_agent_core.controllers.stream import (
            _persist_stream_answer)
        session = self._session()
        self.env['ai.coworker.session.line'].create({
            'session_id': session.id, 'role': 'tool',
            'tool_name': 'odoo_search', 'content': 'träffar',
            'sequence': 5,
        })
        _persist_stream_answer(self.env, session.id, 'Detta är svaret.')
        lines = session.session_line_ids.sorted('sequence')
        assistants = lines.filtered(lambda l: l.role == 'assistant')
        self.assertEqual(len(assistants), 1,
                         'exakt en assistant-rad ska finnas')
        self.assertIn('Detta är svaret.', assistants[0].content)

    def test_answer_sequence_after_tool_lines(self):
        """2.2: assistant-raden hamnar efter verktygsraderna."""
        from odoo.addons.ai_agent_core.controllers.stream import (
            _persist_stream_answer)
        session = self._session()
        self.env['ai.coworker.session.line'].create({
            'session_id': session.id, 'role': 'tool',
            'tool_name': 'odoo_search', 'content': 'x', 'sequence': 9,
        })
        _persist_stream_answer(self.env, session.id, 'svar')
        lines = session.session_line_ids.sorted('sequence')
        assistant = lines.filtered(lambda l: l.role == 'assistant')
        self.assertEqual(assistant[0].sequence, 10,
                         'sekvensen ska följa sessionens högsta värde')
        tool = lines.filtered(lambda l: l.role == 'tool')
        self.assertEqual(tool[0].sequence, 9,
                         'inga befintliga sekvensvärden ska ändras')

    def test_no_answer_marks_explicitly(self):
        """2.1/2.3: tur utan visningsbart innehåll markeras explicit."""
        from odoo.addons.ai_agent_core.controllers.stream import (
            _persist_stream_answer)
        session = self._session()
        _persist_stream_answer(self.env, session.id, '')
        assistants = session.session_line_ids.filtered(
            lambda l: l.role == 'assistant')
        self.assertEqual(len(assistants), 1,
                         'markeringen ska ändå ge en assistant-rad')
        self.assertTrue(assistants[0].content.strip(),
                        'markeringen ska vara läsbar')

    def test_reasoning_used_when_no_answer(self):
        """2.1: narrering används när svarstext saknas."""
        from odoo.addons.ai_agent_core.controllers.stream import (
            _persist_stream_answer)
        session = self._session()
        _persist_stream_answer(self.env, session.id, '',
                               reasoning='Agentens resonemang.')
        assistants = session.session_line_ids.filtered(
            lambda l: l.role == 'assistant')
        self.assertIn('Agentens resonemang.', assistants[0].content)

    def test_finish_reason_and_error_metadata(self):
        """2.3: avslutsorsak + feldetalj skrivs på sessionen."""
        from odoo.addons.ai_agent_core.controllers.stream import (
            _persist_stream_answer)
        session = self._session()
        _persist_stream_answer(
            self.env, session.id, '', finish_reason='max_rounds',
            error='RuntimeError: provider nere')
        self.assertEqual(session.finish_reason, 'max_rounds')
        self.assertIn('provider nere', session.error_detail or '')

    def test_does_not_duplicate_existing_answer(self):
        """2.1: skriver inte en andra assistant-rad i samma tur."""
        from odoo.addons.ai_agent_core.controllers.stream import (
            _persist_stream_answer)
        session = self._session()
        self.env['ai.coworker.session.line'].create({
            'session_id': session.id, 'role': 'assistant',
            'content': 'redan sparad', 'sequence': 3,
        })
        _persist_stream_answer(self.env, session.id, 'ny text')
        assistants = session.session_line_ids.filtered(
            lambda l: l.role == 'assistant')
        self.assertEqual(len(assistants), 1,
                         'servern ska inte dubblera ett befintligt svar')


@tagged('post_install', '-at_install')
class TestThreadSaveResponseIdempotent(common.TransactionCase):
    """3.1: klientens svarspost dubblerar inte serverns rad."""

    def _session(self, name):
        return self.env['ai.coworker.session'].create({
            'name': name, 'status': 'active', 'init_type': 'web_ui',
        })

    def test_same_answer_not_duplicated(self):
        from odoo.addons.ai_agent_core.controllers.stream import (
            _persist_stream_answer)
        session = self._session('idempotent-test')
        _persist_stream_answer(self.env, session.id, 'Samma svar.')
        session._save_client_response(content='Samma svar.')
        assistants = session.session_line_ids.filtered(
            lambda l: l.role == 'assistant')
        self.assertEqual(len(assistants), 1,
                         'identiskt svar ska inte dubbleras')

    def test_new_answer_is_appended(self):
        from odoo.addons.ai_agent_core.controllers.stream import (
            _persist_stream_answer)
        session = self._session('idempotent-test-2')
        _persist_stream_answer(self.env, session.id, 'Första.')
        session._save_client_response(content='Andra, nyare svar.')
        assistants = session.session_line_ids.filtered(
            lambda l: l.role == 'assistant')
        self.assertEqual(len(assistants), 2,
                         'ett annat svar ska appendas')


@tagged('post_install', '-at_install')
class TestCleanupTimeout(common.TransactionCase):
    """4.1: efter-strömmens uppstädning är tidsbegränsad."""

    def test_stuck_cleanup_returns_within_limit(self):
        import time
        from odoo.addons.ai_agent_core.controllers import stream as st

        async def _never_done():
            await asyncio.sleep(3600)

        loop = asyncio.new_event_loop()
        try:
            task = loop.create_task(_never_done())
            start = time.time()
            st._drain_tasks_with_timeout(loop, timeout=0.2)
            elapsed = time.time() - start
            self.assertLess(elapsed, 5.0,
                            'uppstädningen ska återvända inom tidsgränsen')
            task.cancel()
        finally:
            loop.close()
