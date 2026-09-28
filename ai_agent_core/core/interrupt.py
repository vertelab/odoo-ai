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

    OBS: tidigare returnerade `approve_tool()` alltid True — vilket
    auto-godkände även destruktiva verktyg. Det motsade
    hitl-dispatch-routing: en obevakad körning ska INTE kunna kringgå
    gaten, den ska lämna ett ärende efter sig.

    `hitl_request` är en callable (tool_name, risk_level, arguments) →
    som skapar posten. Är den None (t.ex. i enhetstester av loopen)
    behålls det gamla beteendet, så inga befintliga tester krossas.
    """

    def __init__(self, hitl_request=None):
        self.hitl_request = hitl_request

    async def ask(self, question: str, approval_type: str = "", context: str = "", timeout: float = 300) -> dict:
        return {"action": "timeout", "reason": "unattended execution"}

    async def approve_tool(self, tool_name: str, risk_level: str, arguments: dict) -> bool:
        if self.hitl_request is None:
            return True   # inget ärende att skapa — oförändrat beteende
        if risk_level in ('safe', 'read_only'):
            return True   # läsverktyg kräver inget godkännande
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
    - icke-interaktiv (cron, mail, watch, webhook) → RECORD-HITL
      (`AutoInterruptHandler` med `hitl_request`), som skapar en
      `ai.coworker.hitl`-post och INTE blockerar körningen.

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
    return AutoInterruptHandler(hitl_request=hitl_request)
