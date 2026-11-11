# -*- coding: utf-8 -*-
from __future__ import annotations
"""
Agent Loop — Buzz-inspired while-loop (LOOP-001, LOOP-003, LOOP-005, LOOP-007).

LOOP-001: prompt → provider.chat() → execute tools → repeat
LOOP-003: Parallel tool execution, bounded by tool_timeout.
           Errors return strings, never crash the loop.
LOOP-005: Cancel mid-turn — stop LLM call, stop pending tools.
LOOP-007: Parallel tools with configurable max_parallel_tools (semaphore).

No LangGraph. No StateGraph. No LangChain.
Just a while-loop that any senior engineer can read in a sitting.
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Optional

from .provider import (
    AIProvider,
    ChatResponse,
    Message,
    Role,
    TokenEvent,
    ToolCall,
)
from .tools import Tool, ToolRegistry, nats_request_reply
from .permission import (
    PermissionEngine,
    PermissionMode,
    Decision,
    RiskClass,
    classify as risk_classify,
)
from .trust import scan_untrusted, wrap_untrusted

_logger = logging.getLogger(__name__)

# Default för AgentConfig.max_rounds. Ligger som konstant så att
# normaliseringen i __post_init__ och dataclassens fältdefault inte kan
# glida isär.
DEFAULT_MAX_ROUNDS = 20

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass
class AgentConfig:
    """Configuration for an AgentLoop instance."""

    model: str = "gpt-4o"
    system_prompt: str = ""
    temperature: float = 0.7
    max_tokens: int = 4096
    max_rounds: int = DEFAULT_MAX_ROUNDS  # max loop iterations per turn
    max_context_tokens: int = 128_000  # trigger summarization at this threshold
    # Tak för ANTAL meddelanden (chat-session-limits-and-names D1).
    #
    # VARFÖR ett eget tak och inte härlett ur tokens: verktygsrader är korta
    # men många. Sambandet mellan tecken och antal är inte linjärt — 2 121
    # korta rader kan ligga under tokenbudgeten men ändå nekas av providern.
    # Mätt 2026-11-05: session 27753 skickade 2 121 meddelanden till Bifrost,
    # som svarade `400 Bad Request`.
    #
    # 500 är mätt: p90 för sessioner är 277 rader, och de verktygstunga
    # ligger på 488–500. Över det sammanfattas historiken (samma väg som
    # tokenöverskridandet använder), i stället för att requesten nekas.
    max_messages: int = 500
    tool_timeout: float = 30.0  # seconds per tool execution
    llm_timeout: float = 240.0  # seconds per LLM call (research/supervisor lazy providers)
    max_tool_result_chars: int = 8000  # truncate large tool results
    max_parallel_tools: int = 5  # max concurrent tool executions (LOOP-007)
    approval_threshold: int = 2  # risk level requiring approval (HITL-005)
    permission_mode: str = "interactive"  # discuss | plan | interactive | auto | custom
    max_clarifications: int = 3  # max proactive questions per turn (HITL-007)

    # Access-grupper (ai-tool-access-capabilities): Odoo group ids för den
    # användare vars vägnar loopen körs — PermissionEngine nekar gruppbundna
    # verktyg utan korsning (defense-in-depth).
    user_group_ids: tuple = ()

    # NATS executor config (tool-executor-nats)
    nats_api_secret: str = ""  # api_secret for Pi-agent verification
    nats_max_retries: int = 3  # retries before giving up on NATS tool
    nats_timeout: float = 60.0  # seconds per NATS request-reply

    # User context for NATS tool execution (pi-agent-memory-bridge D5)
    nats_user_context: dict = None  # {"user_id": 42, "company_id": 1, "coworker_id": 7, "session_id": 123}

    # Bifrost sessionskorrelation (bifrost-session-lankning): per-anrops-headers
    # som loopen skickar med varje provider-anrop. Sätts av ägaren (ai.coworker)
    # ur sessionens identitet — providern cachas per modell och kan betjäna
    # flera sessioner, så identiteten får ALDRIG sättas på klienten.
    # Tom dict/None → inga extra headers (t.ex. provider utan is_bifrost).
    session_headers: dict = None

    # Utfalls-persistens (utfall-och-tokenmatning D1): anropas vid VARJE
    # returväg ur loopen med det slutliga ChatResponse. Sätts EN gång i
    # ai.coworker._build_loop() — alla åtta körvägar går genom den, så en rad
    # täcker dem alla. Loopen förblir domän-ren (ingen Odoo-import); callbacken
    # äger skrivningen. Samma mönster som denial_callback.
    outcome_callback: callable = None

    # Transaktionshygien (2026-10-08): anropas vid varje punkt där loopen INTE
    # behöver sin databastransaktion öppen — före ett provider-anrop och när
    # rundans verktyg kört klart. Ägaren (controllern som äger cursorn) sätter
    # den till att committa sin cursor.
    #
    # VARFÖR: en långlivad transaktion håller ACCESS SHARE på VARJE tabell dess
    # verktyg läst. En streaming-tur kan pågå i minuter, och en `ALTER TABLE`
    # (moduluppgradering som lägger till ett fält) behöver ACCESS EXCLUSIVE.
    # Odoo avbryter låsväntan efter 15 s (modules/loading.py sätter
    # "SET SESSION lock_timeout = '15s'"), så uppgraderingen faller — och en
    # uppgradering som faller mitt i ett schema står halvfärdig.
    #
    # Mätt på ledningssystem 2026-10-08: en AI-turs transaktion höll
    # AccessShareLock på project_project + project_task + ai_coworker i
    # 35–39 s (idle in transaction medan den väntade på LLM) — exakt de
    # tabeller en moduluppgradering behövde låsa.
    #
    # Loopen förblir domän-ren (ingen Odoo-import); callbacken äger committen.
    # Samma mönster som outcome_callback/denial_callback.
    checkpoint_callback: callable = None

    # HITL-hanterare för den här körningen (init-type-execution-parity 2.1).
    #
    # Sätts av `_build_loop` via `select_interrupt_handler` — den enda
    # beslutsplatsen — så att varje init-typ får rätt mekanism utan att
    # anropsplatsen behöver komma ihåg det. `None` betyder "ingen kanal":
    # ett `needs_user`-beslut nekar då (fail-closed, beslut 2).
    #
    # Ligger i AgentConfig (inte som AgentLoop-argument) därför att
    # `_build_loop` har fem returpunkter — en rad här täcker alla fem, och
    # en framtida sjätte kan inte glömma den.
    interrupt_handler: callable = None

    def __post_init__(self):
        """Gör konfigurationen körbar, oavsett vad anroparen skickade.

        VARFÖR (fix-stream-max-rounds-none): ett `@dataclass`-fält med
        default får `None` när argumentet skickas explicit — defaulten
        gäller bara när argumentet utelämnas. `controllers/stream.py`
        skickade `max_rounds=None` (med en kommentar som påstod att värdet
        härleddes någon annanstans), och det första varvet kastade:

            TypeError: '<' not supported between instances of 'int'
            and 'NoneType'

        i `while round_num < self.config.max_rounds`. Hela webbchatten
        svarade "Anslutningen till AI-servern bröts" på varje fråga.

        `_build_loop()` hade redan en sådan härledning, men den strömmande
        vägen bygger looparna själv och glömde den. Att laga bara den
        anropsplatsen lämnar skyddet på en plats — nästa nya körväg kan
        glömma det igen. Normaliseringen ligger därför i konfigurationen:
        kärnan ska inte kunna bringas i ett tillstånd där dess egen
        loopvillkor är otypat.

        Detta ERSÄTTER INTE `_build_loop`s härledning: den är semantisk
        (härled ur medarbetarens max_iterations, fallback 8), medan detta är
        en säkerhetsventil. Båda behövs.
        """
        if not self.max_rounds or self.max_rounds < 1:
            _logger.warning(
                'AgentConfig.max_rounds=%r is not a positive integer — normalising to %s', self.max_rounds, DEFAULT_MAX_ROUNDS)
            self.max_rounds = DEFAULT_MAX_ROUNDS


# ---------------------------------------------------------------------------
# AgentLoop (LOOP-001, LOOP-003, LOOP-005, LOOP-007)
# ---------------------------------------------------------------------------


class AgentLoop:
    """A Buzz-inspired agent loop.

    Features:
    - Parallel tool execution (LOOP-007) with configurable max_parallel_tools
    - Cancellation support (LOOP-005) via cancel_event
    - Structured logging with per-round timing
    - Context summarization when token budget exceeded (LOOP-004)

    Usage:
        provider = AIProvider(base_url='...', is_bifrost=True)
        tools = ToolRegistry()
        # Verktyg väljs explicit (ai.agent.tool_ids) — inte via builtin_tools()

        loop = AgentLoop(provider=provider, tools=tools, config=AgentConfig())
        result = await loop.run("What is 2+2?")

        # With cancellation:
        loop = AgentLoop(...)
        # Later: loop.cancel()
        result = await loop.run("Long task")  # Will raise CancelledError
    """

    def __init__(
        self,
        provider: AIProvider,
        tools: ToolRegistry,
        config: Optional[AgentConfig] = None,
        interrupt_handler=None,
        permission_engine: Optional[PermissionEngine] = None,
        context_provider: Optional[callable] = None,
        denial_callback: Optional[callable] = None,
        tool_selector=None,
        env=None,
    ):
        self.provider = provider
        self.tools = tools
        # Odoo-miljön, när loopen skapas från en modell. Behövs för att
        # läsa systemparametrar (t.ex. `validate_tool_args`). Loopen är
        # annars domän-neutral och `env` får vara None — då gäller
        # defaultvärdena.
        self.env = env
        self.config = config or AgentConfig()
        # Explicit argument vinner över config (bakåtkompatibelt: de vägar
        # som redan skickar `interrupt_handler` till konstruktorn, t.ex.
        # stream.py och openai_api.py, fortsätter att göra det).
        self.interrupt_handler = (
            interrupt_handler
            if interrupt_handler is not None
            else getattr(self.config, 'interrupt_handler', None)
        )
        self.context_provider = context_provider
        # Grindens spår (init-type-execution-parity 3.4): vilka tool_call_id
        # som passerade `_gate_tool_calls`. `_execute_tool` varnar om den
        # anropas med ett id som inte finns här — det betyder att en NY
        # exekveringsväg har uppstått utan grind, och då ska det synas i
        # loggen i stället för att upptäckas av en säkerhetsgranskning.
        self._gated_call_ids: set = set()
        # Upprepningsskydd (tool-call-quality D6): (verktygsnamn, argument)
        # som redan gett ett FEL i den här turen. Session 28097 skickade
        # `{}` fyra gånger — ett avvisat anrop som bara upprepas äter turer
        # och tokens utan att komma närmare ett svar.
        #
        # Bara anrop som REDAN FELAT registreras: en lyckad sökning får
        # upprepas (samma fråga två gånger kan vara rimlig), och ett rättat
        # anrop är per definition inte identiskt.
        self._failed_calls: set = set()
        # Nekorsak per tool_call_id — används för att skriva ett begripligt
        # TOOL-meddelande till modellen ("denied: <skäl>").
        self._denial_reasons: dict = {}
        # Eskalering efter ett flaggat otrovärdigt resultat
        # (untrusted-tool-output D4). Sätts av `_execute_tool` när skannern
        # ger `hitl`, läses av `_gate_tool_calls`: nästa `EXTERNAL`- eller
        # `WRITE_LOCAL`-anrop kräver mänskligt godkännande även i AUTO.
        # Flaggan gäller ETT anrop — en injektion ska inte låsa agenten för
        # resten av sessionen.
        self._untrusted_escalation: bool = False
        # Tool selection (priority 1): narrow the registry to a task-matched
        # subset before each provider call. None = send everything (old
        # behaviour). Never widens access — only narrows the authorised set.
        self.tool_selector = tool_selector
        # Last selection stats (observability — token delta per call).
        self.tool_selection_stats: list = []
        # Async-ytor (cron/mail/webhook): kallas när ett verktyg nekas av
        # permission engine (t.ex. hårt stopp) — kan dirigera till
        # workspace-approval-kön.
        self.denial_callback = denial_callback

        # Observability: [(tool_name, result_preview, tool_call_id), ...] per
        # execution, read by callers (e.g. ai.coworker.run) for session-line
        # persistence. tool_call_id är OpenAI:s id för anropet — det krävs för
        # att tool-raden ska kunna paras med assistant-radens tool_calls och
        # skickas tillbaka i giltigt format (annars 422 hos strikta providers).
        # Äldre konsumenter packar upp två värden; se _tool_history_triples().
        self.tool_history: list = []
        # Fullständiga verktygsresultat [(tool_name, arguments, result)] —
        # behövs för write-verify (3.2) eftersom tool_history trunkeras.
        self.tool_results: list = []

        # Reasoning- & narrativ-spårning (d): samlar den "gråa" tänketexten
        # (reasoning_content/reasoning) och supervisor-/agentrundornas
        # narrering (undersökning/val före tool-delegation). Exponeras till
        # ai.coworker.run för att spara på session-message-racerna.
        self.reasoning_log: list = []
        self.narration_log: list = []

        # Permission engine (optional — backwards compatible)
        if permission_engine:
            self.permissions = permission_engine
        else:
            # Create one from config so downstream code can always reference it
            try:
                mode = PermissionMode(self.config.permission_mode)
            except ValueError:
                mode = PermissionMode.INTERACTIVE
            self.permissions = PermissionEngine(mode=mode)
            if self.config.user_group_ids:
                self.permissions.user_group_ids = set(self.config.user_group_ids)

        # Cancellation support (LOOP-005)
        self._cancel_event = asyncio.Event()
        self._cancelled = False
        self._partial_results: list[tuple[str, str]] = []

        # Todo list (plan-before-action) — use TodoList from tools
        from .tools import TodoList, planning_tools
        self.todo_list = TodoList()
        # Register planning tools bound to THIS loop's todo list (D6).
        # The tools close over the holder, so two loops in the same worker
        # no longer overwrite each other's state via a module-level global.
        for pt in planning_tools(self.todo_list):
            if pt.name not in self.tools:
                self.tools.register(pt)

    def _finish(self, response):
        """Anropa outcome_callbacken (om satt) och returnera svaret oförändrat.

        Varje returväg ur `run()` går genom denna (utfall-och-tokenmatning D1),
        så utfallet persisteras oavsett VARFÖR körningen slutade. Callbacken får
        aldrig kasta vidare fel — en trasig persistens ska inte fälla körningen
        som redan är klar.
        """
        # Per-varv-räkningen fylls här — `_finish` är den enda returvägen ur
        # `run()`, så ingen avslutsgren kan glömma den (webui-show-work-again
        # 4.1). `round_inputs` samlas i loopen ovan; `rounds` härleds ur den.
        try:
            _rinputs = getattr(self, '_round_inputs', None) or []
            if _rinputs and not getattr(response, 'round_inputs', None):
                response.round_inputs = list(_rinputs)
                response.rounds = len(_rinputs)
        except Exception:
            pass  # best-effort: räkningen får inte fälla avslutet
        cb = getattr(self.config, 'outcome_callback', None)
        if cb:
            try:
                cb(response)
            except Exception:
                _logger.exception('outcome_callback misslyckades')
        return response

    def _checkpoint(self, label: str):
        """Släpp databastransaktionen vid en punkt där loopen inte behöver den.

        Anropas före varje provider-anrop och när rundans verktyg kört klart
        (se config.checkpoint_callback). Ägaren committar sin cursor, så att
        inga lås hålls över LLM-väntan.

        Får aldrig kasta vidare fel: en misslyckad commit ska inte fälla en tur
        som annars fungerar (samma princip som _finish).
        """
        cb = getattr(self.config, 'checkpoint_callback', None)
        if cb:
            try:
                cb(label)
            except Exception:
                _logger.exception(
                    'checkpoint_callback misslyckades (%s)', label)

    def _session_headers(self) -> Optional[dict]:
        """Per-anrops-headers för Bifrost-sessionskorrelation.

        Returnerar `config.session_headers` när providern är en gateway
        (`is_bifrost`), annars None. Providers utan gateway ska inte få några
        `x-bf-*`-headers alls (bifrost-session-lankning krav 3.3).
        """
        if not getattr(self.provider, "is_bifrost", False):
            return None
        return self.config.session_headers or None

    def _tool_defs(self, messages: list[Message]):
        """Build the tool definitions for a provider call.

        Priority 1 (context saving): when a tool_selector is configured, the
        registry is narrowed to a task-matched subset before serialisation.
        The selector never widens access — it only filters the already
        authorised registry — and it fails open (full registry on error).

        The prompt used for selection is the last user message; that is what
        the turn is actually about.
        """
        if len(self.tools) == 0:
            return None
        if self.tool_selector is None:
            return self.tools.to_openai()

        prompt = ""
        for m in reversed(messages):
            if m.role == Role.USER and m.content:
                prompt = m.content
                break

        narrowed = self.tool_selector.select(prompt, self.tools)
        stats = getattr(self.tool_selector, "last_stats", None)
        if stats:
            self.tool_selection_stats.append(stats)
        if len(narrowed) == 0:
            return None
        return narrowed.to_openai()

    def cancel(self) -> None:
        """Signal cancellation. Stops LLM call and pending tools."""
        self._cancelled = True
        self._cancel_event.set()
        _logger.info("AgentLoop: cancel signalled")

    @property
    def is_cancelled(self) -> bool:
        return self._cancelled

    async def run(
        self,
        prompt: str,
        history: Optional[list[Message]] = None,
    ) -> ChatResponse:
        """Run the agent loop for a single user prompt.

        Returns the final ChatResponse when the agent is done.
        """
        messages = list(history) if history else []
        if prompt:
            messages.append(Message(role=Role.USER, content=prompt))
        # Upprepningsskyddet gäller PER TUR (tool-call-quality D6).
        self._failed_calls = set()

        round_num = 0
        total_input_tokens = 0
        total_output_tokens = 0
        # Per-varv-input (webui-show-work-again 4.1): varje varv skickar hela
        # historiken igen, så total_input_tokens är en summa. `run()` är en
        # annan körväg än `run_stream()` men har samma problem — utan
        # uppdelningen ser en tur med åtta rundor ut som en enda enorm prompt.
        round_inputs: list[int] = []
        self._round_inputs = round_inputs
        start_time = time.time()

        while round_num < self.config.max_rounds:
            round_num += 1
            round_start = time.time()

            # -- Cancel check --
            if self._cancelled:
                _logger.info(
                    "AgentLoop cancelled at round %d — %d messages, "
                    "input=%d output=%d tokens, elapsed=%.1fs",
                    round_num, len(messages),
                    total_input_tokens, total_output_tokens,
                    time.time() - start_time,
                )
                return self._finish(ChatResponse(
                    text=f"(cancelled after {round_num} rounds)",
                    input_tokens=total_input_tokens,
                    output_tokens=total_output_tokens,
                    finish_reason="cancelled",
                ))

            _logger.debug(
                "AgentLoop round %d/%d — %d messages, %d tools",
                round_num, self.config.max_rounds,
                len(messages), len(self.tools),
            )

            # -- Context management --
            if self._context_too_large(messages):
                _logger.info(
                    "Context too large (%d msgs, ~%d chars) — summarizing",
                    len(messages),
                    sum(len(m.content or "") for m in messages),
                )
                messages = await self._summarize(messages)

            # -- Context injection (OpenWorker-inspired) --
            if self.context_provider and messages and round_num > 1:
                try:
                    ctx = self.context_provider() or ""
                    if ctx:
                        # Inject as <system-context> into last user message
                        block = f"\n\n<system-context>\n{ctx}\n</system-context>"
                        for i in range(len(messages) - 1, -1, -1):
                            if messages[i].role == Role.USER:
                                messages[i] = Message(
                                    role=messages[i].role,
                                    content=messages[i].content + block,
                                    tool_call_id=messages[i].tool_call_id,
                                    tool_calls=messages[i].tool_calls,
                                    name=messages[i].name,
                                )
                                break
                except Exception:
                    pass  # best-effort: tyst fallback — se etiketteringsregeln

            # Släpp transaktionen innan provider-anropet (kan ta tiotals
            # sekunder) — annars hålls ACCESS SHARE på allt turen läst och en
            # moduluppgradering med DDL faller på Odoos 15 s låstak.
            self._checkpoint('round-start')

            # -- Provider call (with cancel support) --
            tool_defs = self._tool_defs(messages)

            try:
                chat_task = asyncio.create_task(
                    self.provider.chat(
                        model=self.config.model,
                        messages=messages,
                        tools=tool_defs,
                        system_prompt=self.config.system_prompt,
                        temperature=self.config.temperature,
                        max_tokens=self.config.max_tokens,
                        extra_headers=self._session_headers(),
                    )
                )
                cancel_task = asyncio.create_task(self._cancel_event.wait())

                done, _ = await asyncio.wait(
                    [chat_task, cancel_task],
                    timeout=self.config.llm_timeout,
                    return_when=asyncio.FIRST_COMPLETED,
                )

                if cancel_task in done:
                    chat_task.cancel()
                    _logger.info("LLM call cancelled by user")
                    return self._finish(ChatResponse(
                        text="(cancelled)",
                        input_tokens=total_input_tokens,
                        output_tokens=total_output_tokens,
                        finish_reason="cancelled",
                    ))

                if chat_task not in done:
                    chat_task.cancel()
                    _logger.error(
                        "LLM call timed out after %.0fs (round %d)",
                        self.config.llm_timeout, round_num,
                    )
                    return self._finish(ChatResponse(
                        text="Error: LLM call timed out. Please try again.",
                        input_tokens=total_input_tokens,
                        output_tokens=total_output_tokens,
                        finish_reason="timeout",
                    ))

                response = chat_task.result()

            except asyncio.CancelledError:
                _logger.info("LLM call cancelled")
                return self._finish(ChatResponse(
                    text="(cancelled)",
                    input_tokens=total_input_tokens,
                    output_tokens=total_output_tokens,
                    finish_reason="cancelled",
                ))
            except Exception as e:
                _logger.error("LLM call failed: %s", e, exc_info=True)
                return self._finish(ChatResponse(
                    text=f"Error: LLM call failed — {e}",
                    input_tokens=total_input_tokens,
                    output_tokens=total_output_tokens,
                    finish_reason="error",
                ))

            total_input_tokens += response.input_tokens
            total_output_tokens += response.output_tokens
            round_inputs.append(int(response.input_tokens or 0))

            round_elapsed = time.time() - round_start

            # -- Text response (no tool calls) → done --
            if response.text and not response.tool_calls:
                # -- Clarification check (HITL-007) --
                if response.needs_clarification and self.interrupt_handler:
                    clarification_count = getattr(self, '_clarification_count', 0) + 1
                    if clarification_count <= self.config.max_clarifications:
                        self._clarification_count = clarification_count
                        _logger.info(
                            "Agent needs clarification (%d/%d): %s",
                            clarification_count, self.config.max_clarifications,
                            response.clarification_question[:100],
                        )
                        answer = await self.interrupt_handler.ask(
                            question=response.clarification_question,
                            approval_type="clarification",
                            timeout=300,
                        )
                        if answer.get("action") == "answer":
                            messages.append(Message(
                                role=Role.USER,
                                content=f"Clarification: {answer['answer']}",
                            ))
                            continue  # Continue loop with new information
                        # Timeout or deny — return whatever we have
                    else:
                        _logger.info(
                            "Max clarifications (%d) reached — proceeding",
                            self.config.max_clarifications,
                        )

                elapsed = time.time() - start_time
                _logger.info(
                    "AgentLoop done: %d rounds, input=%d output=%d tokens, "
                    "elapsed=%.1fs",
                    round_num, total_input_tokens, total_output_tokens, elapsed,
                )
                response.input_tokens = total_input_tokens
                response.output_tokens = total_output_tokens
                # Den vanligaste vägen (lyckat textsvar) måste också gå genom
                # _finish — annars persisteras aldrig det lyckade utfallet
                # (utfall-och-tokenmatning 3.3).
                return self._finish(response)

            # -- Tool calls → execute in parallel (LOOP-007) --
            if response.tool_calls:
                _logger.info(
                    "AgentLoop round %d: %d tool calls, LLM time=%.1fs",
                    round_num, len(response.tool_calls), round_elapsed,
                )

                # Append assistant message with tool calls
                assistant_msg = Message(
                    role=Role.ASSISTANT,
                    content=response.text or "",
                    tool_calls=[
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.name,
                                "arguments": tc._raw_arguments
                                if hasattr(tc, "_raw_arguments")
                                else str(tc.arguments),
                            },
                        }
                        for tc in response.tool_calls
                    ],
                )
                messages.append(assistant_msg)

                # -- propose_plan interception (PLAN mode) --
                for tc in list(response.tool_calls):
                    if tc.name == "propose_plan":
                        plan_text = tc.arguments.get("plan", "")
                        _logger.info("propose_plan called: %s", plan_text[:100])

                        if self.permissions.mode == PermissionMode.PLAN:
                            # Emit plan for approval
                            if self.interrupt_handler:
                                result = await self.interrupt_handler.ask(
                                    question=f"The agent proposes the following plan:\n\n{plan_text}\n\nApprove?",
                                    approval_type="plan_approval",
                                    timeout=300,
                                )
                                if result.get("action") == "answer" and result.get("answer", "").lower().strip() in (
                                    "ja", "yes", "ok", "approve", "run", "kör"
                                ):
                                    # Switch from PLAN to INTERACTIVE mode
                                    self.permissions.set_mode(PermissionMode.INTERACTIVE)
                                    plan_result = "Plan approved. Switching to interactive mode. Proceed with execution."
                                    _logger.info("Plan approved — mode: %s", self.permissions.mode.value)
                                else:
                                    plan_result = f"Plan not approved: {result.get('answer', result.get('action', 'denied'))}. Revise or ask what to change."
                                    _logger.info("Plan denied")
                            else:
                                # No interrupt handler — auto-approve in non-interactive mode
                                self.permissions.set_mode(PermissionMode.INTERACTIVE)
                                plan_result = "Plan auto-approved (no interactive handler). Switching to interactive mode."
                        else:
                            # Not in PLAN mode — just acknowledge
                            plan_result = f"Not in plan mode (current: {self.permissions.mode.value}). Proceed with execution directly."

                        # Append tool result for propose_plan
                        messages.append(Message(
                            role=Role.TOOL,
                            content=json.dumps({
                                "plan_received": True,
                                "approved": self.permissions.mode != PermissionMode.PLAN,
                                "note": plan_result,
                            }),
                            tool_call_id=tc.id,
                            name=tc.name,
                        ))
                        # Remove propose_plan from the batch — already handled
                        response.tool_calls = [
                            t for t in response.tool_calls if t.id != tc.id
                        ]

                if not response.tool_calls:
                    continue  # All calls were propose_plan — go to next round

                # -- Permission check (HITL-005 + PermissionEngine) --
                # Grinden ligger i `_gate_tool_calls` så att run_stream()
                # använder EXAKT samma logik (init-type-execution-parity 3.2).
                denied_tool_ids = await self._gate_tool_calls(
                    response.tool_calls)
                for tc in response.tool_calls:
                    if tc.id in denied_tool_ids:
                        messages.append(Message(
                            role=Role.TOOL,
                            content=self._denial_message(tc),
                            tool_call_id=tc.id,
                            name=tc.name,
                        ))

                # Remove denied tool calls
                if denied_tool_ids:
                    response.tool_calls = [
                        t for t in response.tool_calls
                        if t.id not in denied_tool_ids
                    ]

                # Execute tools in parallel (LOOP-007)
                if response.tool_calls:
                    results = await self._execute_tools_parallel(response.tool_calls)
                    for tc, result in results:
                        messages.append(Message(
                            role=Role.TOOL,
                            content=result,
                            tool_call_id=tc.id,
                            name=tc.name,
                        ))

                # Rundans DB-arbete är klart — släpp transaktionen innan
                # loopen väntar på providern igen.
                self._checkpoint('tools-done')
                continue  # next round

            # -- Empty response (shouldn't happen) --
            _logger.warning("Empty response from provider — stopping after round %d", round_num)
            return self._finish(ChatResponse(
                text="(no response)",
                input_tokens=total_input_tokens,
                output_tokens=total_output_tokens,
                finish_reason="error",
            ))

        # -- Max rounds exceeded --
        elapsed = time.time() - start_time
        _logger.warning(
            "Max rounds (%d) exceeded — input=%d output=%d tokens, elapsed=%.1fs",
            self.config.max_rounds, total_input_tokens, total_output_tokens, elapsed,
        )
        # Gör ett sista anrop UTAN verktyg så användaren alltid får ett
        # sammanfattande svar (inte bara '(max rounds exceeded — stopping)').
        try:
            synth_messages = messages + [Message(
                role=Role.USER,
                content=(
                    'Now summarise your best answer to the user question above. Answer directly with the conclusion/result — no tools. If you lack enough data, say so honestly and give what you know.'
                ),
            )]
            synth = await self.provider.chat(
                model=self.config.model,
                messages=synth_messages,
                system_prompt=self.config.system_prompt,
                temperature=self.config.temperature,
                max_tokens=self.config.max_tokens,
                extra_headers=self._session_headers(),
            )
            if synth and synth.text:
                return self._finish(ChatResponse(
                    text=synth.text,
                    input_tokens=total_input_tokens + synth.input_tokens,
                    output_tokens=total_output_tokens + synth.output_tokens,
                    finish_reason="max_rounds",
                ))
        except Exception as e:
            # Logga i stället för att tystna (web-ui-stream-turn-persistens
            # 1.1) — fallback-texten nedan returneras ändå.
            _logger.error(
                "max_rounds-avslutets sammanfattning misslyckades (sync): %s",
                e, exc_info=True,
            )
        return self._finish(ChatResponse(
            text="(max rounds exceeded — stopping)",
            input_tokens=total_input_tokens,
            output_tokens=total_output_tokens,
            finish_reason="max_rounds",
        ))

    # -- Tool execution (LOOP-003, LOOP-007) --

    async def _gate_tool_calls(self, tool_calls: list) -> set[str]:
        """Pröva verktygsanropen mot behörighetsgrinden. Returnerar nekade id:n.

        DETTA ÄR DEN ENDA GRINDEN. Både `run()` och `run_stream()` anropar
        den innan något exekveras — tidigare låg logiken inline i `run()`, och
        `run_stream()` (som når samma `_execute_tool`) hade ingen grind alls.
        Det gjorde att `/ai/stream` (`auth='public'`) och `/ai/v1`-strömvägen
        kunde köra `odoo_unlink` — ett hårt stopp — utan godkännande.

        Felklassen är dokumenterad i `docs/failures.md` §10 ("Grind utanför
        loopen"); botemedlet är att grinden ligger i exekveringsvägen, inte
        på en enskild endpoint.

        Tre steg per anrop:
          1. `permissions.evaluate()` — får verktyget köras alls?
          2. `needs_user` → `interrupt_handler.approve_tool()`.
          3. `_untrusted_escalation` satt och anropet är `EXTERNAL` eller
             `WRITE_LOCAL` → `interrupt_handler.approve_tool()` (steg 2:s
             fail-closed-regel gäller även här).

        Steg 2 är FAIL-CLOSED (beslut 2): saknas hanteraren nekas anropet.
        Tidigare löd villkoret `and self.interrupt_handler`, vilket gjorde
        att ett `needs_user`-beslut UTAN hanterare föll igenom och verktyget
        kördes — motsatsen till vad ett godkännandekrav betyder.

        Steg 3 flyttar eskaleringen för ett flaggat otrovärdigt resultat hit
        i stället för in i verktyget (untrusted-tool-output D4): grinden är
        den enda exekveringsvägen, och en eskalering i verktyget hade skapat
        en andra.

        Args:
            tool_calls: providerns `tool_calls` för rundan.

        Returns:
            set[str]: id:n för de anrop som nekades (ska inte exekveras).
        """
        denied: set[str] = set()
        for tc in tool_calls:
            tool = self.tools.get(tc.name)
            # 1. PermissionEngine.evaluate()
            decision = self.permissions.evaluate(
                tc.name, tc.arguments, metadata=tool,
            )

            if not decision.allowed:
                _logger.info(
                    "Permission denied for '%s': %s",
                    tc.name, decision.reason,
                )
                if self.denial_callback:
                    try:
                        self.denial_callback(
                            tc.name, tc.arguments, decision.reason)
                    except Exception:
                        _logger.warning(
                            'Denial callback failed', exc_info=True)
                self._denial_reasons[tc.id] = decision.reason
                denied.add(tc.id)
                continue

            # 2. needs_user → fråga människan (eller neka om ingen kanal)
            if decision.needs_user:
                approved = await self._request_approval(tc, tool)
                if not approved:
                    self._denial_reasons[tc.id] = (
                        'denied by user' if self.interrupt_handler
                        else 'no approval channel on this path')
                    denied.add(tc.id)
                    continue
                # Godkänt — registrera som stående regel om tillämpligt
                self.permissions.allow_tool_for_session(tc.name)

            # 3. Eskalering efter ett flaggat otrovärdigt resultat
            #    (untrusted-tool-output D4).
            #
            #    Ett `hitl`-resultat betyder att ett verktygsresultat bar en
            #    tydlig attack. Nästa konsekventa anrop — `EXTERNAL` (nät) eller
            #    `WRITE_LOCAL` (databasen) — får då INTE köras på agentens eget
            #    initiativ, inte ens i AUTO. Läsvägar påverkas inte: en
            #    injektion som bara får agenten att läsa är inte den skada
            #    lagret finns för att stoppa.
            #
            #    Flaggan förbrukas här — ett anrop, inte en session. Den
            #    nollställs oavsett om anropet godkänns eller nekas: annars
            #    hade ett nekande lämnat flaggan satt och eskaleringen blivit
            #    permanent.
            if self._untrusted_escalation and not decision.needs_user:
                risk = self._risk_class(tc, tool)
                # Hämtningsverktyg (fetch_url, web_search) är LÄSANDE men
                # deras resultat är det som bar injektionen. Utan dem här
                # vore eskaleringen verkningslös för sitt huvudscenario: en
                # sida med inbäddad injektion flaggas korrekt, men nästa
                # hämtning krävde aldrig godkännande.
                from .permission import UNTRUSTED_FETCH_TOOLS
                is_fetch = tc.name in UNTRUSTED_FETCH_TOOLS
                if is_fetch or risk in (RiskClass.EXTERNAL,
                                        RiskClass.WRITE_LOCAL):
                    self._untrusted_escalation = False
                    _logger.warning(
                        "Tool '%s' escalated to human approval: a previous "
                        "tool result was flagged as a prompt-injection "
                        "attempt (risk=%s).",
                        tc.name, risk.value,
                    )
                    approved = await self._request_approval(tc, tool)
                    if not approved:
                        self._denial_reasons[tc.id] = (
                            'denied by user after a flagged tool result'
                            if self.interrupt_handler
                            else 'no approval channel on this path')
                        denied.add(tc.id)
                        continue
                    self.permissions.allow_tool_for_session(tc.name)

        # Grinden har prövat dessa anrop. Spåret läses av `_execute_tool`
        # (defensiv varning) och av strukturtestet i testsviten.
        self._gated_call_ids.update(
            tc.id for tc in tool_calls if tc.id not in denied)
        return denied

    def _risk_class(self, tool_call, tool) -> RiskClass:
        """Anropets effektiva riskklass — samma beräkning som grinden gör.

        `PermissionEngine.evaluate()` räknar ut klassen internt men lämnar
        inte tillbaka den i `Decision`. Eskaleringen behöver den för att
        avgöra om anropet är konsekvent (`EXTERNAL`/`WRITE_LOCAL`), så
        beräkningen upprepas här med samma ingångar — argument-medveten risk
        först, annars den statiska.
        """
        from .permission import _argument_risk

        risk_level = getattr(tool, 'risk_level', 'read_only')
        arg_risk = _argument_risk(tool_call.name, tool_call.arguments)
        if arg_risk is not None:
            return arg_risk
        return risk_classify(tool_call.name, risk_level, tool)

    def _denial_message(self, tool_call) -> str:
        """TOOL-meddelandet som talar om VARFÖR ett anrop nekades.

        Modellen behöver orsaken för att kunna välja en annan väg; ett tomt
        "denied" får den att försöka igen med samma anrop.
        """
        reason = self._denial_reasons.get(tool_call.id, 'denied')
        return f"Tool '{tool_call.name}' was denied: {reason}"

    async def _request_approval(self, tool_call, tool) -> bool:
        """Fråga HITL-kanalen om ett `needs_user`-anrop. Fail-closed.

        Ingen hanterare ⇒ neka. En hanterare som kastar ⇒ neka (en trasig
        godkännandekanal får aldrig bli en tyst auto-godkännande).
        """
        if self.interrupt_handler is None:
            _logger.warning(
                "Tool '%s' requires approval but this execution path has no "
                "approval channel — denying (fail-closed). init_type=%s",
                tool_call.name,
                getattr(self.config, 'init_type', None) or 'unknown',
            )
            return False
        try:
            return bool(await self.interrupt_handler.approve_tool(
                tool_call.name,
                tool.risk_level if tool else "read_only",
                tool_call.arguments,
            ))
        except Exception:
            _logger.exception(
                "Approval channel failed for '%s' — denying (fail-closed)",
                tool_call.name,
            )
            return False

    @staticmethod
    def _call_key(name, args):
        """Stabil nyckel för (verktyg, argument) — för upprepningsskyddet."""
        import json as _json
        try:
            return (name, _json.dumps(args or {}, sort_keys=True,
                                      ensure_ascii=False, default=str))
        except (TypeError, ValueError):
            return (name, repr(args))

    def _validate_required_args(self, tool, args):
        """Kontrollera obligatoriska argument INNAN verktyget körs.

        (tool-call-quality D1/D2.) Returnerar ``''`` när anropet är giltigt,
        annars ett strukturerat fel som JSON-sträng.

        VARFÖR HÄR OCH INTE I VARJE HANDLER: tio handlers validerade redan
        sina egna argument, men inkonsekvent — `okf_search` svarade
        "query is required" (begripligt), `odoo_search({})` svarade
        "Unknown model: " (ett tomt eko). Att laga tio ställen och hoppas på
        den elfte är samma fälla som `_okf_model_is_indexable` var. EN grind
        fångar alla nuvarande och framtida verktyg.

        TOMT ÄR SAKNAT: `{}`, `''`, `None` och `[]` för ett obligatoriskt
        fält räknas som ett saknat argument. Den observerade buggen var inte
        att nyckeln saknades — modellen skickade ett objekt utan nycklar
        (session 28097, fyra gånger). En närvarokontroll hade släppt igenom
        exakt det fallet.

        Bara fält i schemats `required` prövas. Ett verktyg där tom sträng är
        ett giltigt värde (t.ex. `period=""` i okf_search = "använd default")
        påverkas därför inte, eftersom fältet inte är obligatoriskt.
        """
        params = getattr(tool, 'parameters', None) or {}
        required = params.get('required') or []
        if not required:
            return ''
        # Systemparametern gör grinden avstängningsbar utan kodändring
        # (task 2.4) — en rollback ska inte kräva en deploy. Loopen har
        # ingen `env` (den är domän-neutral), så flaggan läses ur en
        # kontextnyckel som anroparen sätter; utan den gäller defaulten.
        enabled = getattr(self, 'validate_tool_args', True)
        env = getattr(self, 'env', None)
        if env is not None:
            try:
                enabled = env['ir.config_parameter'].sudo().get_param(
                    'ai_agent_core.validate_tool_args', 'True') == 'True'
            except Exception:  # noqa: BLE001 — utan env gäller defaulten
                pass
        if not enabled:
            return ''

        properties = params.get('properties') or {}
        for name in required:
            value = args.get(name)
            empty = (
                value is None
                or (isinstance(value, str) and not value.strip())
                or (isinstance(value, (list, dict, tuple)) and not value)
            )
            if not empty:
                continue
            prop = properties.get(name) or {}
            # Exempel: schemats egen beskrivning om den bär ett, annars
            # fältets typ. Vi hittar inte på ett värde som kan vara fel.
            example = prop.get('example') or ''
            expected = prop.get('description') or prop.get('type') or 'a value'
            from .tools import _missing_argument_error
            # `next_step` kommer ur SCHEMAT när det finns: ett verktyg som
            # vet vad som är nästa steg (t.ex. describe_model → peka på
            # describe_available) ska inte skymmas av grindens generiska
            # text. Grinden fyller bara i när schemat tiger.
            #
            # FYND 2026-11-11 (första driftkörningen): den generiska texten
            # skrev över handlerns specifika, så felet tappade sitt bästa
            # råd. Fältet läses ur `properties[name].next_step` — det är
            # där ett schema kan bära det utan att handlern behöver köras.
            next_step = prop.get('next_step') or (
                'Pass a non-empty value for %r and try again — the tool '
                'was not executed.' % name)
            return _missing_argument_error(
                name, expected, example=example, tool_name=tool.name,
                next_step=next_step,
            ).to_json()
        return ''

    async def _execute_tools_parallel(
        self, tool_calls: list
    ) -> list[tuple]:
        """Execute multiple tool calls in parallel with concurrency limit.

        Returns list of (tool_call, result_string) tuples.
        """
        if not tool_calls:
            return []

        sem = asyncio.Semaphore(self.config.max_parallel_tools)

        async def bounded_execute(tc):
            async with sem:
                if self._cancelled:
                    return tc, f"Tool '{tc.name}' cancelled"
                result = await self._execute_tool(tc)
                return tc, result

        tasks = [bounded_execute(tc) for tc in tool_calls]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Handle exceptions from gather
        output = []
        for i, r in enumerate(results):
            if isinstance(r, Exception):
                output.append((tool_calls[i], f"Error executing tool: {r}"))
            else:
                output.append(r)

        return output

    async def _execute_tool(self, tool_call) -> str:
        """Execute a single tool call with timeout and error handling.

        Supports executor routing:
        - executor="local" → tool.execute() (befintligt beteende)
        - executor="nats"  → _execute_via_nats() (tool-executor-nats)

        Grinden ligger i `_gate_tool_calls` och anropas av båda loopvägarna
        INNAN hit. Den defensiva varningen nedan fångar en framtida väg som
        glömmer det: hellre en loggrad än en tyst ogrindad exekvering.
        """
        if tool_call.id not in self._gated_call_ids:
            _logger.warning(
                "Tool '%s' (id=%s) executed without passing the permission "
                "gate — a call path bypasses _gate_tool_calls. See "
                "docs/failures.md section 10.",
                tool_call.name, tool_call.id,
            )
        tool = self.tools.get(tool_call.name)
        if not tool:
            return f"Error: unknown tool '{tool_call.name}'"

        # NATS executor routing
        if tool.executor == "nats":
            result = await self._execute_via_nats(tool, tool_call.arguments)
            # Fjärde elementet = argumenten (webui-show-work-again 3.1).
            # Utan dem går en tool-rad inte att granska i efterhand: raden
            # säger att `odoo_search` kördes men inte vad den sökte på, och
            # argumenten kan inte rekonstrueras ur resultatet.
            self.tool_history.append(
                (tool_call.name, str(result)[:500], tool_call.id or '',
                 dict(tool_call.arguments or {})))
            self.tool_results.append(
                (tool_call.name, dict(tool_call.arguments or {}), result))
            return self._frame_untrusted(tool, result)

        # Local execution (default, existing behavior)
        tool_start = time.time()
        _logger.debug("Executing tool: %s", tool_call.name)

        # Normalisera argument: vissa LLM:er nestlar allt under en
        # 'arguments'-nyckel → execute(**{"arguments": {...}}) kraschar.
        args = tool_call.arguments
        if isinstance(args, dict) and 'arguments' in args and \
                set(args.keys()) == {'arguments'}:
            args = args['arguments']
        if not isinstance(args, dict):
            args = {}

        # GRINDEN LIGGER EFTER NORMALISERINGEN (tool-call-quality D1/D2).
        # Ordningen är inte kosmetik: `{'arguments': {'model': 'x'}}` måste
        # packas upp först, annars ser kontrollen `arguments` som ett okänt
        # fält och avvisar ett anrop som är giltigt.
        # Upprepningsskyddet (D6): exakt samma anrop som redan felat i
        # den här turen körs inte igen.
        call_key = self._call_key(tool_call.name, args)
        if call_key in self._failed_calls:
            repeated = (
                "This exact call already failed in this turn and was not "
                "executed again. Change the arguments — repeating it will "
                "give the same error.")
            self.tool_history.append(
                (tool_call.name, repeated[:500], tool_call.id or '',
                 dict(args or {})))
            self.tool_results.append((tool_call.name, dict(args or {}),
                                      repeated))
            return repeated

        invalid = self._validate_required_args(tool, args)
        if invalid:
            self._failed_calls.add(call_key)
            self.tool_history.append(
                (tool_call.name, str(invalid)[:500], tool_call.id or '',
                 dict(args or {})))
            self.tool_results.append((tool_call.name, dict(args or {}), invalid))
            return self._frame_untrusted(tool, invalid)

        try:
            execute_task = asyncio.create_task(
                tool.execute(**args)
            )
            cancel_task = asyncio.create_task(self._cancel_event.wait())

            done, _ = await asyncio.wait(
                [execute_task, cancel_task],
                timeout=self.config.tool_timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )

            if cancel_task in done:
                execute_task.cancel()
                return f"Tool '{tool_call.name}' cancelled"

            if execute_task not in done:
                execute_task.cancel()
                # Ett timeout är ett fel — upprepa inte exakt samma anrop.
                self._failed_calls.add(call_key)
                return f"Error: tool '{tool_call.name}' timed out after {self.config.tool_timeout}s"

            result = execute_task.result()
            tool_elapsed = time.time() - tool_start
            self.tool_history.append(
                (tool_call.name, str(result)[:500], tool_call.id or '',
                 dict(args or {})))
            self.tool_results.append(
                (tool_call.name, dict(args or {}), result))
            _logger.debug(
                "Tool '%s' completed in %.2fs, result length=%d",
                tool_call.name, tool_elapsed, len(result),
            )

        except asyncio.CancelledError:
            return f"Tool '{tool_call.name}' cancelled"
        except Exception as e:
            _logger.warning("Tool '%s' failed: %s", tool_call.name, e)
            # Ett kastat fel är också ett fel — samma anrop igen ger samma
            # krasch (tool-call-quality D6).
            self._failed_calls.add(call_key)
            return f"Error executing '{tool_call.name}': {e}"

        # Chunkning av stora verktygsresultat — se `_chunk_tool_result`.
        # Detta är INTE en sammanfattning: ingen LLM anropas, inget skrivs
        # till `session.summary`. Det är en ren längdbegränsning för att
        # resultatet ska rymmas i kontextfönstret (okf-recall-path 4.5).
        return self._frame_untrusted(tool, self._chunk_tool_result(result))

    def _frame_untrusted(self, tool: 'Tool', result: str) -> str:
        """Skanna och rama in ett otrovärdigt verktygsresultat.

        Ligger i `_execute_tool` — den gemensamma exekveringsvägen — så att
        BÅDE `run()` och `run_stream()` får inramningen (untrusted-tool-output
        3.3). Att lägga den på en enskild endpoint hade gett nästa väg ingen.

        Betrott resultat (`trust != "untrusted"`) returneras oförändrat och
        skannas inte: Odoo-data är data som en användare med behörighet redan
        kan se, och att skanna den hade kostat tid utan att skydda något.

        Ett `hitl`-utfall sätter `self._untrusted_escalation`, som grinden
        läser för nästa konsekventa anrop (D4). Flaggan sätts här och inte i
        `_execute_tools_parallel`: den senare kör anropen samtidigt, och en
        flagga satt inuti en parallell gren hade påverkat syskonanrop som
        redan passerat grinden — utan att påverka nästa runda, vilket är det
        som spelar roll.

        Args:
            tool: Verktyget som gav resultatet.
            result: Resultatet, redan klippt.

        Returns:
            Resultatet inramat när verktyget är otrovärdigt, annars orört.
        """
        if getattr(tool, 'trust', 'tool') != 'untrusted' or not result:
            return result
        scan = scan_untrusted(result)
        if scan.is_flagged:
            # Logga VERDICT och KATEGORIER — aldrig innehållet. Ett
            # verktygsresultat kan bära användarens data, och en logg som
            # citerar injektionen blir själv en injektionskanal för den som
            # läser loggen.
            _logger.warning(
                "Untrusted tool result flagged: tool=%s verdict=%s "
                "categories=%s matches=%d",
                tool.name, scan.verdict, ','.join(scan.categories),
                len(scan.matches),
            )
            if scan.verdict == 'hitl':
                self._untrusted_escalation = True
        return wrap_untrusted(tool.name, result, scan)

    def _chunk_tool_result(self, result: str) -> str:
        """Klipp ett stort verktygsresultat så att både början och slutet bevaras.

        Avgränsad från sammanfattning (okf-recall-path 4.5): denna funktion
        är deterministisk, gratis och rör aldrig `session.summary`. Tidigare
        låg logiken inline i `_execute_tool`, vilket gjorde att "att klippa ett
        resultat" och "att sammanfatta en session" såg ut som samma sak.
        """
        if len(result) <= self.config.max_tool_result_chars:
            return result
        half = self.config.max_tool_result_chars // 2
        return (
            result[:half]
            + "\n... (truncated %d chars) ...\n"
            % (len(result) - self.config.max_tool_result_chars)
            + result[-half:]
        )

    async def _execute_via_nats(self, tool: 'Tool', args: dict) -> str:
        """Execute a tool via NATS request-reply delegation.

        Sends the tool call to a Pi-agent using NATS request-reply.
        The Pi-agent verifies api_secret, executes the tool using its
        skills, and replies with the result.

        Args:
            tool: The Tool dataclass with NATS config
            args: Tool arguments from the LLM

        Returns:
            Result string from the Pi-agent, or error message
        """
        import json

        nats_timeout = getattr(tool, 'nats_timeout', 30) or self.config.nats_timeout
        max_retries = self.config.nats_max_retries
        subject = getattr(tool, 'nats_subject', 'pi.task.do')
        skills = getattr(tool, 'nats_skills', '')

        # Build NATS payload
        payload = {
            "tool": tool.name,
            "args": args,
            "skills": [s.strip() for s in skills.split(",") if s.strip()],
            "api_secret": self.config.nats_api_secret,
        }
        # Agent-identitet (pi-agent-agent-identity): verktygets ägande
        # agent följer med som en HINT. Executorn prioriterar ett
        # uppdrags-specifikt agent-fält över denna.
        nats_agent = getattr(tool, 'nats_agent', '') or ''
        if nats_agent:
            payload["agent"] = nats_agent
        # Include user context for pi-agent (pi-agent-memory-bridge D5)
        if self.config.nats_user_context:
            payload["context"] = self.config.nats_user_context

        last_error = ""
        for attempt in range(1, max_retries + 1):
            if self._cancelled:
                return f"Tool '{tool.name}' cancelled (NATS, attempt {attempt})"

            _logger.info(
                "NATS tool '%s' → %s (attempt %d/%d, timeout=%ds)",
                tool.name, subject, attempt, max_retries, nats_timeout,
            )

            result = await nats_request_reply(
                subject=subject,
                payload=payload,
                timeout=nats_timeout,
            )

            # Check if result is an error message
            if result.startswith("NATS request timed out") or result.startswith("NATS request failed"):
                last_error = result
                _logger.warning(
                    "NATS tool '%s' attempt %d/%d failed: %s",
                    tool.name, attempt, max_retries, result[:100],
                )
                continue

            # Success
            _logger.info(
                "NATS tool '%s' completed (attempt %d/%d, result=%d bytes)",
                tool.name, attempt, max_retries, len(result),
            )
            return result

        # All retries exhausted
        error_msg = (
            f"NATS tool '{tool.name}' failed after {max_retries} attempts. "
            f"Last error: {last_error}. "
            f"Pi-agent may be offline — check nats connection and agent status."
        )
        _logger.error(error_msg)
        return error_msg

    # -- Context management (LOOP-004) --

    def _context_too_large(self, messages: list[Message]) -> bool:
        """Pröva historiken mot BÅDA taken: antal meddelanden och tokens.

        VARFÖR två tak (chat-session-limits-and-names D1): providers nekar
        för stora requests oavsett tokenantal. En historik med tusentals
        korta verktygsrader kan ligga under tokenbudgeten men ändå avvisas
        med `400 Bad Request` — mätt 2026-11-05 med 2 121 meddelanden.

        Loggar vilket tak som slog till, så driftfelsökning ser skillnaden
        mellan "för många meddelanden" och "för mycket text".
        """
        max_msgs = getattr(self.config, 'max_messages', 0) or 0
        if max_msgs and len(messages) > max_msgs:
            _logger.info(
                'Context too large: %d meddelanden > taket %d — sammanfattar',
                len(messages), max_msgs)
            return True
        total_chars = sum(len(m.content or "") for m in messages)
        estimated_tokens = total_chars // 4  # rough: ~4 chars per token
        if estimated_tokens > self.config.max_context_tokens:
            _logger.info(
                'Context too large: ~%d tokens > budget %d — sammanfattar',
                estimated_tokens, self.config.max_context_tokens)
            return True
        return False

    async def _summarize(self, messages: list[Message]) -> list[Message]:
        """Komprimera konversationshistoriken för att rymmas i kontextbudgeten.

        OBS: detta är INTE sessionens eftermäle (okf-recall-path D4/4.5).
        Resultatet är ett efemärt systemmeddelande som bara lever i denna
        körning — det persisteras aldrig på `session.summary` och anropas
        aldrig av stängnings- eller cron-vägen. Sessionens eftermäle skrivs
        av `ai.coworker.session._write_final_summary`.

        Prompten är svensk för språkkonsekvens (session-close krav 5):
        eftermälen ska vara jämförbara och sökbara med svensk fulltextsökning.
        """
        if len(messages) < 4:
            return messages  # nothing to summarize

        # Keep the most recent messages, summarize everything before
        keep_recent = 4
        to_summarize = messages[:-keep_recent]
        recent = messages[-keep_recent:]

        summary_prompt = (
            "Summarise the conversation below. Keep all key facts, decisions and context. Be concise but complete.\n\n"
            + "\n".join(
                f"{m.role.value}: {m.content[:500]}"
                for m in to_summarize
                if m.content
            )
        )

        try:
            response = await asyncio.wait_for(
                self.provider.chat(
                    model=self.config.model,
                    messages=[Message(role=Role.USER, content=summary_prompt)],
                    system_prompt="Du sammanfattar konversationer. Var koncis.",
                    temperature=0.3,
                    max_tokens=2048,
                    extra_headers=self._session_headers(),
                ),
                timeout=self.config.llm_timeout,
            )
            summary = f"[Tidigare konversation, sammanfattad: {response.text}]"
        except Exception as e:
            _logger.warning("Summarization failed: %s — keeping recent messages", e)
            summary = "[Summary failed — keeping the latest context]"

        summary_msg = Message(role=Role.SYSTEM, content=summary)
        return [summary_msg] + recent


# ---------------------------------------------------------------------------
# Streaming variant
# ---------------------------------------------------------------------------

class StreamingAgentLoop(AgentLoop):
    """AgentLoop with streaming support.

    Yields TokenEvents as they arrive from the provider.
    """

    async def run_stream(
        self,
        prompt: str,
        history: Optional[list[Message]] = None,
    ):
        """Run the agent loop and yield TokenEvents for streaming.

        Yields tokens, tool_call_start, tool_call_end, and done events.
        """
        messages = list(history) if history else []
        if prompt:
            messages.append(Message(role=Role.USER, content=prompt))
        # Upprepningsskyddet gäller PER TUR (tool-call-quality D6).
        self._failed_calls = set()

        round_num = 0

        # Verklig token-usage ackumuleras över alla rundor (providern
        # rapporterar per anrop) och sätts på den slutgiltiga done-händelsen
        # så web-chatten kan bokföra korrekt input/output per meddelande.
        total_input_tokens = 0
        total_output_tokens = 0

        while round_num < self.config.max_rounds:
            round_num += 1

            if self._context_too_large(messages):
                messages = await self._summarize(messages)

            tool_defs = self._tool_defs(messages)

            # Stream from provider
            text_buffer = ""
            round_tokens: list[str] = []
            tool_calls_seen: list[dict] = []
            # Per-varv-input (webui-show-work-again 4.1): varje varv skickar
            # hela historiken igen, så `total_input_tokens` är en summa.
            # Utan uppdelningen ser en tur med åtta rundor ut som en enda
            # enorm prompt (mätt: 104 437 input-tokens, session 27953).
            round_inputs: list[int] = []

            # Släpp transaktionen innan vi väntar på providern — strömmen kan
            # pågå i tiotals sekunder och låsen ska inte sitta under tiden.
            self._checkpoint('round-start')

            async for event in self.provider.chat_stream(
                model=self.config.model,
                messages=messages,
                tools=tool_defs,
                system_prompt=self.config.system_prompt,
                temperature=self.config.temperature,
                max_tokens=self.config.max_tokens,
                extra_headers=self._session_headers(),
            ):
                if event.type == "token":
                    # Buffra rundans text — först vid done vet vi om det är
                    # narrering (debug) eller slutgiltigt svar (token).
                    text_buffer += event.token
                    round_tokens.append(event.token)

                elif event.type == "thinking":
                    # Modellens "gråa" tänketext (reasoning_content/reasoning)
                    # — samla för att lagra på session-meddelandet OCH skicka
                    # vidare så webbchatten kan visa den medan modellen tänker.
                    #
                    # Utan `yield` var grenen en återvändsgränd: texten hamnade
                    # i `reasoning_log` men nådde aldrig klienten, så
                    # `controllers/stream.py`s `thinking`-gren (som funnits
                    # hela tiden) var död kod. Under modellens betänketid
                    # fanns därför inget att visa alls — spinnern snurrade i
                    # tystnad, vilket såg ut som att något gått fel
                    # (webui-show-work-again).
                    if event.token:
                        self.reasoning_log.append(event.token)
                    yield event

                elif event.type == "tool_call_start":
                    tool_calls_seen.append({
                        "id": event.tool_call.id,
                        "name": event.tool_call.name,
                        "arguments": {},
                    })
                    yield event

                elif event.type == "tool_call_end":
                    if tool_calls_seen:
                        tool_calls_seen[-1]["arguments"] = event.tool_call.arguments
                    yield event

                elif event.type == "done":
                    total_input_tokens += event.input_tokens or 0
                    total_output_tokens += event.output_tokens or 0
                    round_inputs.append(int(event.input_tokens or 0))
                    if tool_calls_seen:
                        # Rundans text var NARRERING (agentens resonemang) —
                        # sänd som debug, inte som svar till användaren.
                        if text_buffer.strip():
                            self.narration_log.append(text_buffer.strip())
                            yield TokenEvent(
                                type="debug", token=text_buffer.strip())
                        # Exekvera verktygen och samla källor
                        assistant_msg = Message(
                            role=Role.ASSISTANT,
                            content=text_buffer,
                            tool_calls=[
                                {
                                    "id": tc["id"],
                                    "type": "function",
                                    "function": {
                                        "name": tc["name"],
                                        "arguments": json.dumps(tc["arguments"]),
                                    },
                                }
                                for tc in tool_calls_seen
                            ],
                        )
                        messages.append(assistant_msg)

                        # -- Permission check (init-type-execution-parity 3.3) --
                        #
                        # SAMMA grind som run(). Fram till nu exekverade
                        # denna väg verktyg direkt: `/ai/stream`
                        # (`auth='public'`) och `/ai/v1`-strömmen kunde köra
                        # `odoo_unlink` — ett hårt stopp — utan godkännande.
                        # `handler` skickades in och `get_pending()` pollades,
                        # men `approve_tool()` anropades aldrig.
                        _gate_calls = [
                            ToolCall(id=tc["id"], name=tc["name"],
                                     arguments=tc["arguments"])
                            for tc in tool_calls_seen
                        ]
                        _denied_ids = await self._gate_tool_calls(_gate_calls)

                        for tc in tool_calls_seen:
                            if tc["id"] in _denied_ids:
                                _denial = self._denial_message(
                                    ToolCall(id=tc["id"], name=tc["name"],
                                             arguments=tc["arguments"]))
                                messages.append(Message(
                                    role=Role.TOOL,
                                    content=_denial,
                                    tool_call_id=tc["id"],
                                    name=tc["name"],
                                ))
                                # Klienten får se nekandet — annars ser en
                                # webb-användare bara att inget hände.
                                yield TokenEvent(
                                    type="needs_approval",
                                    token=json.dumps({
                                        "tool": tc["name"],
                                        "tool_call_id": tc["id"],
                                        "reason": self._denial_reasons.get(
                                            tc["id"], "denied"),
                                    }),
                                )
                                continue
                            # SSE-progress for NATS tools
                            tool_obj = self.tools.get(tc["name"])
                            if tool_obj and tool_obj.executor == "nats":
                                yield TokenEvent(
                                    type="tool_progress",
                                    token=json.dumps({
                                        "executor": "nats",
                                        "tool": tc["name"],
                                        "status": "pending",
                                        "message": f"Waiting for the Pi agent ({tc['name']})...",
                                    }),
                                )
                            result = await self._execute_tool(
                                ToolCall(id=tc["id"], name=tc["name"], arguments=tc["arguments"])
                            )
                            messages.append(Message(
                                role=Role.TOOL,
                                content=result,
                                tool_call_id=tc["id"],
                                name=tc["name"],
                            ))
                            # Samla käll-URL:er (web_search/fetch_url-resultat)
                            for url in self._extract_source_urls(result, tc):
                                yield TokenEvent(type="source", token=url)

                        tool_calls_seen = []
                        text_buffer = ""
                        round_tokens = []
                        # Rundans DB-arbete är klart — släpp transaktionen
                        # innan loopen går vidare.
                        self._checkpoint('tools-done')
                        # break (inte continue!) — continue skulle bara hämta
                        # nästa event ur samma stream; en dubbel-done från
                        # providern skulle då avsluta loopen innan runda 2.
                        break  # → while-loopen startar nästa runda

                    else:
                        # SLUTGILTIGT SVAR — strömma den buffrade texten
                        for t in round_tokens:
                            yield TokenEvent(type="token", token=t)
                        # Sätt ackumulerad usage på den slutgiltiga done —
                        # klienten bokför riktiga input/output-tokens.
                        event.input_tokens = total_input_tokens
                        event.output_tokens = total_output_tokens
                        # Per-varv-uppdelningen (webui-show-work-again 4.1):
                        # `total_input_tokens` är summan över varven, och
                        # utan den här listan går talet inte att förstå.
                        event.rounds = len(round_inputs) or 1
                        event.round_inputs = list(round_inputs)
                        # Utfalls-persistens även på streaming-vägen (3.4):
                        # callbacken ser samma slutliga utfall som run().
                        self._finish(event)
                        yield event
                        return

        # max_rounds nådd: gör ett sista anrop UTAN verktyg så användaren
        # alltid får ett sammanfattande svar (tidigare: tomt avslut → klienten
        # visade bara narreringen utan slutsats).
        #
        # Ett fel här får INTE sväljas tyst (web-ui-stream-turn-persistens
        # 1.1): den tysta except:en gjorde att done emit:ades med noll tokens
        # och turen saknade assistantsvar helt. Felet loggas och bärs vidare
        # på done-händelsen så anroparen kan skriva ett ärligt avslut.
        summary_error = ""
        try:
            async for event in self.provider.chat_stream(
                model=self.config.model,
                messages=messages + [Message(
                    role=Role.USER,
                    content=(
                        'Now summarise your best answer to the user question above. Answer directly with the conclusion/result — no tools. If you lack enough data, say so honestly and give what you know.'
                    ),
                )],
                system_prompt=self.config.system_prompt,
                temperature=self.config.temperature,
                max_tokens=self.config.max_tokens,
                extra_headers=self._session_headers(),
            ):
                if event.type == "token":
                    yield event
                elif event.type == "done":
                    total_input_tokens += event.input_tokens or 0
                    total_output_tokens += event.output_tokens or 0
                    event.input_tokens = total_input_tokens
                    event.output_tokens = total_output_tokens
                    yield event
                    return
        except Exception as e:
            summary_error = "%s: %s" % (type(e).__name__, e)
            _logger.error(
                "max_rounds-avslutets sammanfattning misslyckades "
                "(input=%d output=%d): %s",
                total_input_tokens, total_output_tokens, summary_error,
                exc_info=True,
            )
        yield self._finish(TokenEvent(
            type="done", finish_reason="max_rounds",
            input_tokens=total_input_tokens, output_tokens=total_output_tokens,
            error=summary_error))

    @staticmethod
    def _extract_source_urls(result: str, tc: dict) -> list[str]:
        """Extrahera käll-URL:er ur ett verktygsresultat + verktygsargument.

        Används för att visa en utfällbar käll-lista i chatten och för att
        spara källorna på sessionens meddelande.
        """
        import re
        urls = set()
        for m in re.findall(r'https?://[^\s\)\]"\']+', result or ''):
            u = m.rstrip('.,;:')
            if u.startswith(('http://', 'https://')):
                urls.add(u)
        args = tc.get('arguments') or {}
        if isinstance(args, dict) and args.get('url'):
            urls.add(str(args['url']))
        return sorted(urls)


def tool_history_entries(history):
    """Normalisera tool_history-poster till (name, preview, call_id, arguments).

    tool_history bär sedan 2026-11-05 fyra värden — argumenten tillkom så att
    en tool-rad ska gå att granska: raden säger annars att `odoo_search`
    kördes men inte vad den sökte på, och argumenten kan inte rekonstrueras
    ur resultatet (webui-show-work-again 3.1).

    Bakåtkompatibel: poster med två eller tre värden (skrivna före ändringen,
    eller av tredjeparts-loopar) får tomma fält i stället för att krascha
    uppackningen — samma princip som när call_id infördes 2026-10-08.

    Använd denna i stället för att packa upp tuplen direkt.
    """
    out = []
    for entry in history or []:
        if not entry:
            continue
        name = entry[0]
        preview = entry[1] if len(entry) > 1 else ''
        call_id = entry[2] if len(entry) > 2 else ''
        arguments = entry[3] if len(entry) > 3 else {}
        out.append((name, preview, call_id or '', arguments or {}))
    return out


def tool_history_triples(history):
    """Bakåtkompatibel vy: (name, preview, call_id).

    Behålls för anropare som bara behöver de tre första fälten. Ny kod ska
    använda `tool_history_entries` — den bär även argumenten.
    """
    return [(n, p, c) for (n, p, c, _a) in tool_history_entries(history)]
