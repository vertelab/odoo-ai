# -*- coding: utf-8 -*-
"""Strukturtest: varje körväg har samma godkännandegrind.

Bakgrund (init-type-execution-parity, 2026-11-01): `AgentLoop` har två
körvägar. Bara `run()` prövade `PermissionEngine` och `interrupt_handler`;
`run_stream()` exekverade verktyg direkt på `core/loop.py:1089`. Följden var
att de hårda stoppen (`odoo_call_method`, `odoo_unlink`) kunde köras utan
mänskligt godkännande via web-UI-chatten (`/ai/stream`) och `/ai/v1`-strömmen.

`docs/failures.md` §10 namnger felklassen — *"Grind utanför loopen … ett
strukturtest som kräver att `evaluate` anropas **före** `execute` i varje
väg"* — men fixen i `d8f6c982` (A3) lagade bara `/ai/v1/tools/run`, som är en
tredje väg till samma `_execute_tool`.

Det här testet bevakar fyra saker:

  1. Varje väg som exekverar ett verktyg har passerat grinden först.
     (Skyddar mot NÄSTA väg, inte bara de två vi känner.)
  2. Varje init-typ i `INIT_TYPES` får en interrupt-hanterare.
  3. Ett hårt stopp nekas på den strömmande vägen — inte bara på den
     synkrona.
  4. `needs_user` utan hanterare nekar (fail-closed), det kör inte.

Körs med: checkmodule -d <db> -m ai_agent_core -t -l test
"""

import asyncio
import inspect

from odoo.tests import common, tagged

from odoo.addons.ai_agent_core.core.interrupt import (
    AutoInterruptHandler,
    InterruptHandler,
    select_interrupt_handler,
)
from odoo.addons.ai_agent_core.core.loop import (
    AgentConfig,
    AgentLoop,
    StreamingAgentLoop,
)
from odoo.addons.ai_agent_core.core.permission import (
    PermissionEngine,
    PermissionMode,
)
from odoo.addons.ai_agent_core.core.provider import TokenEvent, ToolCall
from odoo.addons.ai_agent_core.core.tools import Tool, ToolRegistry


# ---------------------------------------------------------------------------
# Hjälpare
# ---------------------------------------------------------------------------

def _registry(*tools):
    reg = ToolRegistry()
    for t in tools:
        reg.register(t)
    return reg


def _tool(name, risk_level='read_only', result='ok', calls=None):
    """Ett verktyg som bokför varje exekvering i `calls`."""
    async def _handler(**kwargs):
        if calls is not None:
            calls.append(name)
        return result

    return Tool(
        name=name,
        description='testverktyg',
        parameters={'type': 'object', 'properties': {}},
        handler=_handler,
        risk_level=risk_level,
    )


class _OneToolProvider:
    """Provider som begär ETT verktyg i första rundan, sedan svarar.

    Fungerar för både `chat()` (synkron) och `chat_stream()` (strömmande).
    """

    def __init__(self, tool_name):
        self.tool_name = tool_name
        self.rounds = 0

    async def chat(self, **kwargs):
        from odoo.addons.ai_agent_core.core.provider import ChatResponse
        self.rounds += 1
        if self.rounds == 1:
            return ChatResponse(
                text='',
                tool_calls=[ToolCall(id='c1', name=self.tool_name,
                                     arguments={})],
                input_tokens=1, output_tokens=1,
                finish_reason='tool_calls')
        return ChatResponse(text='klart', input_tokens=1, output_tokens=1,
                            finish_reason='stop')

    async def chat_stream(self, **kwargs):
        self.rounds += 1
        if kwargs.get('tools') and self.rounds == 1:
            yield TokenEvent(
                type='tool_call_start',
                tool_call=ToolCall(id='c1', name=self.tool_name, arguments={}))
            yield TokenEvent(
                type='tool_call_end',
                tool_call=ToolCall(id='c1', name=self.tool_name, arguments={}))
            yield TokenEvent(type='done', finish_reason='tool_calls',
                             input_tokens=1, output_tokens=1)
            return
        yield TokenEvent(type='token', token='klart')
        yield TokenEvent(type='done', finish_reason='stop',
                         input_tokens=1, output_tokens=1)

    async def aclose(self):
        return None


def _run(coro):
    """Kör en coroutine i en färsk event loop (Odoo-tester är synkrona)."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# 1. Varje väg har en grind
# ---------------------------------------------------------------------------

@tagged('post_install', '-at_install')
class TestEveryPathIsGated(common.TransactionCase):
    """Strukturtest: ingen väg exekverar ett verktyg utan att ha gatat det."""

    def test_execute_tool_records_gate(self):
        """3.4: `_execute_tool` bokför att anropet passerat grinden.

        Grinden sätter `self._gated_call_ids`; `_execute_tool` läser den.
        Utan bokföringen kan testet nedan inte skilja "gated" från "ogated".
        """
        calls = []
        loop = AgentLoop(
            provider=_OneToolProvider('t'),
            tools=_registry(_tool('t', calls=calls)),
            config=AgentConfig(model='m', system_prompt='s', max_rounds=2),
        )
        self.assertTrue(
            hasattr(loop, '_gated_call_ids'),
            '_execute_tool måste kunna se vilka anrop som passerat grinden '
            '(_gated_call_ids saknas)')

    def test_run_path_gates_before_execute(self):
        """`run()` exekverar bara efter `_gate_tool_calls`."""
        calls = []
        loop = AgentLoop(
            provider=_OneToolProvider('t'),
            tools=_registry(_tool('t', calls=calls)),
            config=AgentConfig(model='m', system_prompt='s', max_rounds=2),
        )
        _run(loop.run('hej', history=[]))
        self.assertEqual(calls, ['t'], 'run() skulle kört verktyget en gång')
        self.assertIn('c1', loop._gated_call_ids,
                      'run() exekverade utan att ha gatat anropet')

    def test_run_stream_path_gates_before_execute(self):
        """`run_stream()` exekverar bara efter `_gate_tool_calls`.

        Det här är regressionen för F1: före fixen fanns noll anrop av
        `self.permissions` i `run_stream`.
        """
        calls = []
        loop = StreamingAgentLoop(
            provider=_OneToolProvider('t'),
            tools=_registry(_tool('t', calls=calls)),
            config=AgentConfig(model='m', system_prompt='s', max_rounds=2),
        )
        events = []

        async def _collect():
            async for ev in loop.run_stream('hej', history=[]):
                events.append(ev)

        _run(_collect())
        self.assertEqual(calls, ['t'], 'run_stream() skulle kört verktyget')
        self.assertIn('c1', loop._gated_call_ids,
                      'run_stream() exekverade utan att ha gatat anropet')

    def test_no_loop_path_executes_without_gating(self):
        """Strukturtestet: varje `_execute_tool`-anropare går via grinden.

        Läser källkoden i stället för att köra den — det fångar en NY väg
        innan den hunnit få ett eget test. Varje förekomst av
        `_execute_tool(` / `_execute_tools_parallel(` måste ligga i en metod
        som också anropar `_gate_tool_calls`, eller vara metodens egen
        definition.
        """
        src = inspect.getsource(inspect.getmodule(AgentLoop))
        tree = __import__('ast').parse(src)

        offenders = []
        for node in __import__('ast').walk(tree):
            if not isinstance(node, (__import__('ast').FunctionDef,
                                     __import__('ast').AsyncFunctionDef)):
                continue
            body_src = __import__('ast').get_source_segment(src, node) or ''
            executes = ('_execute_tool(' in body_src
                        or '_execute_tools_parallel(' in body_src)
            # Definitionerna själva räknas inte som anrop, och inte heller
            # `bounded_execute`: den är den inre arbetsfunktionen i
            # `_execute_tools_parallel` — den anropas BARA därifrån, efter
            # att grinden redan prövat hela batchen. Att kräva en grind där
            # skulle vara att kräva två grindar för samma anrop.
            is_definition = node.name in ('_execute_tool',
                                          '_execute_tools_parallel',
                                          'bounded_execute')
            if executes and not is_definition \
                    and '_gate_tool_calls' not in body_src:
                offenders.append(node.name)

        self.assertEqual(
            offenders, [],
            'Dessa metoder exekverar verktyg utan att först anropa '
            '_gate_tool_calls: %s. Grinden ska vara ett delat steg, inte '
            'kopieras per väg (docs/failures.md §10).' % offenders)


# ---------------------------------------------------------------------------
# 2. Varje init-typ har en hanterare
# ---------------------------------------------------------------------------

@tagged('post_install', '-at_install')
class TestEveryInitTypeHasHandler(common.TransactionCase):
    """`select_interrupt_handler` är enda konstruktionsplatsen."""

    def test_every_init_type_resolves_to_a_handler(self):
        """Alla tolv init-typer får en hanterare — ingen får None."""
        from odoo.addons.ai_agent_core.models.ai_coworker import INIT_TYPES

        self.assertEqual(len(INIT_TYPES), 12,
                         'INIT_TYPES ändrades — uppdatera tabellen i '
                         'design beslut 3 och det här testet samtidigt')

        for code, _label in INIT_TYPES:
            handler = select_interrupt_handler(
                code,
                hitl_request=lambda *a, **k: None,
                env=self.env,
            )
            self.assertIsInstance(
                handler, InterruptHandler,
                'init_type=%r fick ingen hanterare (%r)' % (code, handler))

    def test_interactive_types_pause(self):
        """Interaktiva typer pausar — de skapar ingen record-HITL."""
        for code in ('server_action', 'powerbox', 'manual', 'openai_api'):
            handler = select_interrupt_handler(code, env=self.env)
            self.assertNotIsInstance(
                handler, AutoInterruptHandler,
                'init_type=%r ska pausa (interaktiv), inte skapa record-HITL'
                % code)

    def test_non_interactive_types_use_record_hitl(self):
        """cron/mail/watch/webhook/heartbeat använder record-HITL."""
        for code in ('cron', 'mail', 'watch', 'webhook', 'heartbeat'):
            handler = select_interrupt_handler(
                code, hitl_request=lambda *a, **k: None, env=self.env)
            self.assertIsInstance(
                handler, AutoInterruptHandler,
                'init_type=%r ska använda record-HITL' % code)

    def test_discuss_init_type_gets_discuss_handler(self):
        """chat/channel får Discuss-hanteraren när kanalkontext finns."""
        from odoo.addons.ai_agent_core.core.interrupt import (
            DiscussInterruptHandler)
        channel = self.env['discuss.channel'].sudo().create({
            'name': 'parity-test-kanal',
        })
        bot = self.env['res.users'].sudo().search([], limit=1)
        for code in ('chat', 'channel'):
            handler = select_interrupt_handler(
                code, discuss_channel=channel, discuss_bot_user=bot,
                env=self.env)
            self.assertIsInstance(
                handler, DiscussInterruptHandler,
                'init_type=%r ska få Discuss-hanteraren när kanalen finns'
                % code)


# ---------------------------------------------------------------------------
# 3. Hårt stopp nekas på den strömmande vägen
# ---------------------------------------------------------------------------

@tagged('post_install', '-at_install')
class TestStreamHardStopDenied(common.TransactionCase):
    """F1-regressionen: hårda stopp gäller även i en SSE-ström."""

    def _stream_events(self, loop):
        events = []

        async def _collect():
            async for ev in loop.run_stream('hej', history=[]):
                events.append(ev)

        _run(_collect())
        return events

    def test_odoo_unlink_not_executed_on_stream(self):
        """`odoo_unlink` (hårt stopp) får inte exekveras via run_stream.

        Grinden nekar (INTERACTIVE + hårt stopp + ingen godkänd begäran),
        och ett `needs_approval`-event ska ha emitterats.
        """
        calls = []
        loop = StreamingAgentLoop(
            provider=_OneToolProvider('odoo_unlink'),
            tools=_registry(_tool('odoo_unlink', risk_level='destructive',
                                  calls=calls)),
            config=AgentConfig(model='m', system_prompt='s', max_rounds=2),
        )
        # Ingen hanterare → fail-closed (beslut 2). Grinden nekar.
        loop.interrupt_handler = None
        events = self._stream_events(loop)

        self.assertEqual(calls, [],
                         'odoo_unlink exekverades på den strömmande vägen — '
                         'det hårda stoppet gäller inte där')
        # Nekandet ska SYNAS för klienten: ett `needs_approval`-event med
        # orsaken. Utan det ser en webb-användare bara att inget hände.
        denials = [ev for ev in events
                   if getattr(ev, 'type', '') == 'needs_approval']
        self.assertTrue(
            denials,
            'Inget needs_approval-event emitterades (events: %s)'
            % [getattr(ev, 'type', '') for ev in events])
        self.assertIn('odoo_unlink', str(denials[0].token))

    def test_read_only_tool_still_passes_on_stream(self):
        """Grinden får inte bli ett hinder för läsande verktyg."""
        calls = []
        loop = StreamingAgentLoop(
            provider=_OneToolProvider('odoo_search'),
            tools=_registry(_tool('odoo_search', risk_level='read_only',
                                  calls=calls)),
            config=AgentConfig(model='m', system_prompt='s', max_rounds=2),
        )
        loop.interrupt_handler = None
        self._stream_events(loop)
        self.assertEqual(calls, ['odoo_search'],
                         'Ett läsande verktyg ska passera utan hanterare')


# ---------------------------------------------------------------------------
# 4. needs_user utan hanterare = neka (fail-closed)
# ---------------------------------------------------------------------------

@tagged('post_install', '-at_install')
class TestNoHandlerDenies(common.TransactionCase):
    """Beslut 2: frånvaro av godkännandekanal är inte ett godkännande."""

    def test_write_tool_denied_without_handler(self):
        """Ett skrivande verktyg nekas när `interrupt_handler` är None."""
        calls = []
        loop = AgentLoop(
            provider=_OneToolProvider('odoo_write'),
            tools=_registry(_tool('odoo_write', risk_level='write',
                                  calls=calls)),
            config=AgentConfig(model='m', system_prompt='s', max_rounds=2),
        )
        loop.interrupt_handler = None
        loop.permissions = PermissionEngine(mode=PermissionMode.INTERACTIVE)
        _run(loop.run('hej', history=[]))

        self.assertEqual(
            calls, [],
            'Skrivande verktyg kördes utan godkännandekanal — fail-open. '
            'Ett `needs_user` utan hanterare ska neka.')

    def test_read_only_tool_passes_without_handler(self):
        """Läsande verktyg passerar — annars tystnar all drift."""
        calls = []
        loop = AgentLoop(
            provider=_OneToolProvider('odoo_search'),
            tools=_registry(_tool('odoo_search', risk_level='read_only',
                                  calls=calls)),
            config=AgentConfig(model='m', system_prompt='s', max_rounds=2),
        )
        loop.interrupt_handler = None
        _run(loop.run('hej', history=[]))
        self.assertEqual(calls, ['odoo_search'])

    def test_timeout_denies_without_hanging(self):
        """4.2: en timeout i godkännandet ger `denied`, inte hängning."""
        from odoo.addons.ai_agent_core.core.interrupt import (
            WebUIInterruptHandler)

        handler = WebUIInterruptHandler('parity-timeout-test', env=self.env)
        calls = []
        loop = AgentLoop(
            provider=_OneToolProvider('odoo_write'),
            tools=_registry(_tool('odoo_write', risk_level='write',
                                  calls=calls)),
            config=AgentConfig(model='m', system_prompt='s', max_rounds=2),
        )
        loop.interrupt_handler = handler
        loop.permissions = PermissionEngine(mode=PermissionMode.INTERACTIVE)

        # Korta ned väntan så testet inte tar 120 s.
        async def _fast_ask(*args, **kwargs):
            return {'action': 'timeout', 'reason': 'test'}

        handler.ask = _fast_ask
        _run(loop.run('hej', history=[]))
        self.assertEqual(calls, [],
                         'En timeout ska neka, inte köra verktyget')
