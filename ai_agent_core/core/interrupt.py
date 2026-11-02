# -*- coding: utf-8 -*-
"""
Interrupt Handler — Human-in-the-Loop (HITL-001 to HITL-006).

HITL-001: InterruptHandler ABC
HITL-002: DiscussInterruptHandler — waits for channel message
HITL-004: AutoInterruptHandler — always approve (cron/server-action)
HITL-005: Approval threshold — risk_level per tool
HITL-006: Mid-turn steering — drain_steer()
"""

import asyncio
import json
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Risk levels (HITL-005)
# ---------------------------------------------------------------------------

RISK_LEVELS = {
    "safe": 0,        # Read-only lookups, never require approval
    "read_only": 1,   # Reads data, no side effects
    "write": 2,       # Modifies existing data
    "destructive": 3,  # Deletes data
    "execute": 4,     # Runs arbitrary code
}

APPROVAL_THRESHOLD_DEFAULT = 2  # Require approval for write and above


def needs_approval(risk_level: str, threshold: int = APPROVAL_THRESHOLD_DEFAULT) -> bool:
    """Check if a tool at this risk level requires human approval."""
    level = RISK_LEVELS.get(risk_level, 1)
    return level >= threshold


# ---------------------------------------------------------------------------
# ApprovalRequest — en fråga som väntar på svar (tools-run-approval-channel)
# ---------------------------------------------------------------------------


@dataclass
class ApprovalRequest:
    """En godkännandebegäran som väntar på en människa.

    Används av `/ai/v1/tools/run` när grinden säger `needs_user`: i stället
    för att neka blint registreras en begäran som klienten kan läsa via
    `/ai/interrupt/poll` och svara på via `/ai/interrupt/respond`.

    `arg_keys` innehåller **nycklar**, aldrig värden. En begäran ska visa
    VILKA fält anropet rör, inte vad de innehåller — annars läcker
    verktygsargument (kunduppgifter, belopp, personnummer) ut i en kanal
    som pollas av klienten.
    """

    request_id: str
    tool_name: str
    arg_keys: list = field(default_factory=list)
    risk_class: str = ''
    reason: str = ''
    created: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        """Formen som `/ai/interrupt/poll` skickar till klienten."""
        return {
            'type': 'needs_approval',
            'request_id': self.request_id,
            'tool_name': self.tool_name,
            'arg_keys': list(self.arg_keys),
            'risk_class': self.risk_class,
            'reason': self.reason,
            'created': self.created,
        }


# ---------------------------------------------------------------------------
# InterruptHandler ABC (HITL-001)
# ---------------------------------------------------------------------------


class InterruptHandler(ABC):
    """Abstract base for human-in-the-loop interrupt handlers.

    Three methods:
    - ask(): BLOCKING — pause loop, wait for human response
    - approve_tool(): BLOCKING — ask if a specific tool call may proceed
    - drain_steer(): NON-BLOCKING — fetch queued mid-turn messages
    """

    @abstractmethod
    async def ask(
        self,
        question: str,
        approval_type: str = "",
        context: str = "",
        timeout: float = 300,
    ) -> dict:
        """Pause the agent loop and wait for human input.

        Returns:
            {"action": "answer", "answer": "..."} — human responded
            {"action": "timeout", "reason": "..."} — no response in time
            {"action": "deny", "reason": "..."} — human denied
        """
        ...

    @abstractmethod
    async def approve_tool(
        self,
        tool_name: str,
        risk_level: str,
        arguments: dict,
    ) -> bool:
        """Ask if a tool call may proceed.

        Returns True if approved, False if denied.
        """
        ...

    @abstractmethod
    async def drain_steer(self) -> list[str]:
        """Non-blocking: fetch any queued mid-turn messages.

        Buzz pattern: messages injected without restarting the loop.
        Returns list of message strings (empty if none).
        """
        ...


# ---------------------------------------------------------------------------
# AutoInterruptHandler (HITL-004)
# ---------------------------------------------------------------------------


class AutoInterruptHandler(InterruptHandler):
    """Handler för obevakad körning som materialiserar RECORD-HITL.

    Används av icke-interaktiva init-typer (cron, mail, watch, webhook).
    Den blockerar aldrig: när ett verktyg kräver godkännande skapas en
    `ai.coworker.hitl`-post och körningen fortsätter (nekas tills vidare)
    i stället för att vänta på en människa.

    OBS: tidigare returnerade `approve_tool()` alltid True när `hitl_request`
    var None — vilket auto-godkände även destruktiva verktyg. Det motsade
    hitl-dispatch-routing: en obevakad körning ska INTE kunna kringgå gaten.

    B4 (ai-agent-core-fault-remediation): reservgrenen är BORTTAGEN. Saknas
    `hitl_request` finns ingen mottagare för ärendet — då nekar vi med
    orsaken "no approval recipient" i stället för att godkänna. Fail-closed:
    en obevakad körning som inte kan lämna ett ärende efter sig får inte
    heller utföra handlingen.

    Konsekvens i drift: cron-körningar som anropar godkännandekrävande
    verktyg nekas nu (tidigare släpptes de igenom). Det är avsiktligt —
    se `docs/failures.md`.
    """

    def __init__(self, hitl_request=None):
        self.hitl_request = hitl_request

    async def ask(self, question: str, approval_type: str = "", context: str = "", timeout: float = 300) -> dict:
        return {"action": "timeout", "reason": "unattended execution"}

    async def approve_tool(self, tool_name: str, risk_level: str, arguments: dict) -> bool:
        if risk_level in ('safe', 'read_only'):
            return True   # läsverktyg kräver inget godkännande
        if self.hitl_request is None:
            # Ingen mottagare för ärendet → ingen kan godkänna → neka.
            _logger.warning(
                'AutoInterruptHandler: nekar %s (%s) — no approval recipient '
                '(hitl_request saknas i denna körväg)', tool_name, risk_level)
            return False
        # Destruktivt/skrivande: materialisera ärendet och neka tills vidare.
        try:
            self.hitl_request(tool_name, risk_level, arguments)
        except Exception as e:  # noqa: BLE001 — HITL får aldrig krascha loopen
            _logger.warning('AutoInterruptHandler: kunde inte skapa '
                            'HITL-ärende för %s: %s', tool_name, e)
        return False

    async def drain_steer(self) -> list[str]:
        return []


# ---------------------------------------------------------------------------
# DiscussInterruptHandler (HITL-002)
# ---------------------------------------------------------------------------

class DiscussInterruptHandler(InterruptHandler):
    """Human-in-the-loop via Odoo discuss.channel.

    When the agent needs input:
    1. Posts a question as a message in the channel
    2. Waits for the next human message in that channel
    3. Returns the human's response

    Mid-turn steer: drain_steer() reads recent channel messages
    without blocking.
    """

    def __init__(self, channel, bot_user, env):
        self.channel = channel
        self.bot_user = bot_user
        self.env = env
        self._last_seen_message_id = channel.message_ids[-1].id if channel.message_ids else 0
        self._steer_buffer: list[str] = []

    async def ask(
        self,
        question: str,
        approval_type: str = "",
        context: str = "",
        timeout: float = 300,
    ) -> dict:
        """Post question in channel and wait for human response."""
        # Post the question
        self.channel.message_post(
            body=f"🤖 **Agent behöver input:**\n\n{question}",
            message_type='comment',
        )

        # Wait for response
        start = time.time()
        while time.time() - start < timeout:
            await asyncio.sleep(2)

            # Check for new human messages
            messages = self.env['mail.message'].search([
                ('id', '>', self._last_seen_message_id),
                ('model', '=', 'discuss.channel'),
                ('res_id', '=', self.channel.id),
            ], order='id asc')

            for msg in messages:
                self._last_seen_message_id = max(self._last_seen_message_id, msg.id)
                # Skip bot's own messages
                if msg.author_id == self.bot_user.partner_id:
                    continue
                # Found human response
                body = msg.body or ""
                # Strip HTML
                if hasattr(body, 'striptags'):
                    body = body.striptags()
                return {"action": "answer", "answer": body.strip()}

        return {"action": "timeout", "reason": f"no response in {timeout}s"}

    async def approve_tool(
        self,
        tool_name: str,
        risk_level: str,
        arguments: dict,
    ) -> bool:
        """Ask for approval for a specific tool call."""
        result = await self.ask(
            question=f"Får jag köra verktyget **{tool_name}**?\n"
                     f"Risknivå: {risk_level}\n"
                     f"Parametrar: {arguments}",
            approval_type="tool_approval",
            timeout=120,
        )
        if result["action"] == "answer":
            answer = result["answer"].lower().strip()
            return answer in ("ja", "yes", "ok", "kör", "approve", "godkänn")
        return False  # timeout = deny

    async def drain_steer(self) -> list[str]:
        """Non-blocking: fetch recent channel messages as steer input."""
        messages = self.env['mail.message'].search([
            ('id', '>', self._last_seen_message_id),
            ('model', '=', 'discuss.channel'),
            ('res_id', '=', self.channel.id),
        ], order='id asc', limit=10)

        steer = []
        for msg in messages:
            self._last_seen_message_id = max(self._last_seen_message_id, msg.id)
            if msg.author_id == self.bot_user.partner_id:
                continue
            body = msg.body or ""
            if hasattr(body, 'striptags'):
                body = body.striptags()
            if body.strip():
                steer.append(body.strip())

        return steer


# ---------------------------------------------------------------------------
# WebUIInterruptHandler (HITL-003) — placeholder
# ---------------------------------------------------------------------------

class WebUIInterruptHandler(InterruptHandler):
    """Human-in-the-loop via web UI (SSE + HTTP POST).

    Flow:
    1. Agent needs input → emit SSE event "needs_input"
    2. Frontend shows dialog → user types response
    3. Frontend POSTs to /ai/session/{uuid}/respond
    4. Handler resolves with the response
    """

    def __init__(self, session_uuid: str, env=None):
        self.session_uuid = session_uuid
        self.env = env
        self._pending: dict = {}
        self._response: dict | None = None
        self._steer_buffer: list[str] = []

        # Väntande godkännanden (tools-run-approval-channel 2.1). Nyckeln är
        # `request_id`, värdet är en `ApprovalRequest`. `/ai/v1/tools/run`
        # registrerar en begäran här när grinden säger `needs_user`, och
        # `/ai/interrupt/poll` levererar den till klienten.
        #
        # Skilt från `_pending`: `get_pending()` TÖMMER `_pending` (den är
        # en engångshändelse för SSE), medan en godkännandebegäran måste
        # kunna läsas om — klienten kan poll:a flera gånger innan
        # användaren har svarat.
        self.pending_approvals: dict = {}
        # Svar per request_id: {'approved': bool, 'reason': str}
        self._approval_responses: dict = {}

    def emit_sse(self, event_type: str, data: dict) -> None:
        """Store pending interrupt for SSE polling."""
        key = f"ai_interrupt_{self.session_uuid}"
        self._pending = {
            "type": event_type,
            "data": data,
            "timestamp": time.time(),
            "session_uuid": self.session_uuid,
        }

    def get_pending(self) -> dict | None:
        """Get and clear pending interrupt (for SSE poll)."""
        pending = self._pending
        self._pending = {}
        return pending if pending.get("type") else None

    # ── Väntande godkännanden (tools-run-approval-channel) ──

    def register_approval(self, request):
        """Registrera en väntande godkännandebegäran.

        Begäran ligger kvar tills den besvaras eller tidsgränsen går ut —
        till skillnad från `_pending`, som töms vid första läsningen.
        """
        self.pending_approvals[request.request_id] = request
        return request

    def get_approvals(self) -> list:
        """Alla väntande begäranden, i skapelseordning."""
        return sorted(
            self.pending_approvals.values(), key=lambda r: r.created)

    def answer_approval(self, request_id, approved, reason=''):
        """Registrera ett svar på en begäran.

        Returns:
            bool: True om begäran fanns och svaret togs emot.
        """
        if request_id not in self.pending_approvals:
            return False
        self._approval_responses[request_id] = {
            'approved': bool(approved),
            'reason': reason or '',
        }
        self.pending_approvals.pop(request_id, None)
        return True

    def take_approval_response(self, request_id):
        """Hämta och ta bort ett svar. None = inget svar ännu."""
        return self._approval_responses.pop(request_id, None)

    def set_response(self, response: str) -> None:
        """Receive human response from POST endpoint."""
        self._response = {"action": "answer", "answer": response}

    async def ask(self, question: str, approval_type: str = "", context: str = "", timeout: float = 300) -> dict:
        """Emit SSE event and wait for POST response."""
        self.emit_sse("needs_input", {
            "question": question,
            "approval_type": approval_type,
            "context": context,
        })

        self._response = None
        start = time.time()
        while time.time() - start < timeout:
            await asyncio.sleep(1)
            if self._response:
                result = self._response
                self._response = None
                return result

        return {"action": "timeout", "reason": f"no response in {timeout}s"}

    async def approve_tool(self, tool_name: str, risk_level: str, arguments: dict) -> bool:
        self.emit_sse("needs_approval", {
            "tool_name": tool_name,
            "risk_level": risk_level,
            "arguments": arguments,
        })

        result = await self.ask(
            question=f"Approve tool: {tool_name}?",
            approval_type="tool_approval",
            timeout=120,
        )
        if result["action"] == "answer":
            return result["answer"].lower().strip() in ("ja", "yes", "ok", "approve")
        return False

    async def drain_steer(self) -> list[str]:
        """Return queued steer messages and clear buffer."""
        steers = list(self._steer_buffer)
        self._steer_buffer.clear()
        return steers

    def queue_steer(self, message: str) -> None:
        """Queue a mid-turn steer message."""
        self._steer_buffer.append(message)


# ---------------------------------------------------------------------------
# OpenAIInterruptHandler (HITL-008) — pausa via OpenAI tool_calls
# ---------------------------------------------------------------------------

class AgentLoopPaused(Exception):
    """Signal: loopen pausad för HITL — returnera tool_calls till klienten.

    Kastas av OpenAIInterruptHandler.ask()/approve_tool().
    Färdas naturligt upp genom AgentLoop.run() (interrupt-anropen ligger
    utanför try/except) → fångas av openai-controllern som returnerar
    tool_calls i OpenAI-svar.
    """

    def __init__(self, tool_calls: list[dict], state: dict):
        self.tool_calls = tool_calls      # OpenAI-format tool_calls
        self.state = state                # {kind, question, tool, ...}
        super().__init__(
            f"AgentLoop paused for HITL: {state.get('kind', 'unknown')}")


class OpenAIInterruptHandler(InterruptHandler):
    """HITL via OpenAI tool_calls — pausar loopen, låter klienten svara.

    Används av openai_api-init-typen (/ai/openai/<id>/v1/chat/completions)
    där klienten (Pi-agent, Cline, Continue.dev) är orkestratör.
    ask()/approve_tool() kastar AgentLoopPaused med tool_calls som
    klienten exekverar via ctx.ui (confirm/input) och svarar på med
    role:"tool"-meddelanden i nästa request.
    """

    def __init__(self):
        self.paused = False

    async def ask(
        self,
        question: str,
        approval_type: str = "",
        context: str = "",
        timeout: float = 300,
    ) -> dict:
        """Pausa — returnera en tool_call som klienten visar och svarar på."""
        self.paused = True
        raise AgentLoopPaused(
            tool_calls=[{
                "id": f"call_hitl_{int(time.time())}",
                "type": "function",
                "function": {
                    "name": "request_hitl_input",
                    "arguments": json.dumps({
                        "question": question,
                        "approval_type": approval_type,
                        "context": context,
                    }),
                },
            }],
            state={
                "kind": "ask",
                "question": question,
                "approval_type": approval_type,
            },
        )

    async def approve_tool(
        self,
        tool_name: str,
        risk_level: str,
        arguments: dict,
    ) -> bool:
        """Pausa — fråga om verktyget får köras."""
        self.paused = True
        raise AgentLoopPaused(
            tool_calls=[{
                "id": f"call_approve_{int(time.time())}",
                "type": "function",
                "function": {
                    "name": "request_hitl_approval",
                    "arguments": json.dumps({
                        "tool": tool_name,
                        "risk_level": risk_level,
                        "arguments": arguments,
                        "question": f"Godkänn verktyg {tool_name}?",
                    }),
                },
            }],
            state={
                "kind": "approve_tool",
                "tool": tool_name,
                "arguments": arguments,
            },
        )

    async def drain_steer(self) -> list[str]:
        """Inga köade styrmeddelanden via OpenAI-kanalen."""
        return []


# ---------------------------------------------------------------------------
# Val av HITL-mekanism per init-typ (external-agent-runtime D7/D8)
# ---------------------------------------------------------------------------

# Init-typer där en människa står vid rodret och kan svara inom rimlig tid.
# Övriga är obevakade: de får inte blockera på en människa.
INTERACTIVE_INIT_TYPES = (
    'server_action', 'powerbox', 'web_ui', 'chat', 'channel',
    'openai_api', 'manual',
)

# Init-typer som körs utan någon som kan svara just nu. De får record-HITL:
# ett ärende i godkännandekön, ingen blockering (init-type-execution-parity
# 2.2 — watch och heartbeat saknades i tabellen och föll därför i den
# underförstådda else-grenen, vilket gjorde dem till en oavsiktlig
# tredje kategori: INTERACTIVE-läge med handler=None → fail-open).
NON_INTERACTIVE_INIT_TYPES = (
    'cron', 'mail', 'watch', 'webhook', 'heartbeat',
)


def select_interrupt_handler(init_type, hitl_request=None, web_ui_session=None,
                             discuss_channel=None, discuss_bot_user=None,
                             env=None):
    """Välj HITL-mekanism UTIFRÅN INIT-TYPEN — aldrig utifrån agenten.

    Detta är den ENDA beslutsplatsen (hitl-dispatch-routing). Valet beror
    inte på något "användare närvarande"-tillstånd, utan på vilken kanal
    körningen kom in genom:

    - interaktiv (server_action, powerbox, web_ui, chat, channel,
      openai_api, manual) → PAUSAD HITL via OpenAI `tool_calls`
      (`OpenAIInterruptHandler` → `AgentLoopPaused`); körningen återupptas
      i samma session när svaret kommer.
    - icke-interaktiv (cron, mail, watch, webhook, heartbeat) →
      RECORD-HITL (`AutoInterruptHandler` med `hitl_request`), som skapar
      en `ai.coworker.hitl`-post och INTE blockerar körningen.

    Varje init-typ i `INIT_TYPES` måste hamna i någon av de två listorna —
    en typ som hamnar i ingen av dem får en hanterare som nekar allt, vilket
    ser ut som en trasig kanal i stället för ett säkerhetsbeslut.
    `tests/test_execution_parity.py` bevakar att så inte sker.

    Args:
        init_type: str — coworkerns init-typ.
        hitl_request: callable(tool_name, risk_level, arguments) som
            skapar record-HITL-posten. Krävs för den icke-interaktiva vägen.
        web_ui_session: session-uuid för WebUI-vägen (valfritt).
        discuss_channel: discuss.channel för Discuss-vägen (valfritt).
        discuss_bot_user: bot-användaren för Discuss-vägen (valfritt).
        env: Odoo-env för de rikare handlerarna (valfritt).

    Returns:
        En `InterruptHandler`.
    """
    if init_type in INTERACTIVE_INIT_TYPES:
        # web_ui och chat har egna, rikare handlers när deras kontext finns;
        # annars OpenAI-vägen (klienten är orkestratör).
        if init_type == 'web_ui' and web_ui_session:
            return WebUIInterruptHandler(web_ui_session, env=env)
        if init_type in ('chat', 'channel') and discuss_channel \
                and discuss_bot_user:
            return DiscussInterruptHandler(
                discuss_channel, discuss_bot_user, env)
        return OpenAIInterruptHandler()
    # Icke-interaktiv: record-HITL, blockerar aldrig.
    #
    # En okänd init-typ hamnar också här. Det är avsiktligt fail-closed:
    # `AutoInterruptHandler` utan `hitl_request` NEKAR (B4), så en ny
    # init-typ som glöms bort stoppas i stället för att köra ogrindat.
    return AutoInterruptHandler(hitl_request=hitl_request)
