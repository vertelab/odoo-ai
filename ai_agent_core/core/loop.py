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
    classify as risk_classify,
)

_logger = logging.getLogger(__name__)

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
    max_rounds: int = 20  # max loop iterations per turn
    max_context_tokens: int = 128_000  # trigger summarization at this threshold
    tool_timeout: float = 15.0  # seconds per tool execution
    llm_timeout: float = 120.0  # seconds per LLM call
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
        tools.register_many(builtin_tools())

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
    ):
        self.provider = provider
        self.tools = tools
        self.config = config or AgentConfig()
        self.interrupt_handler = interrupt_handler
        self.context_provider = context_provider
        # Async-ytor (cron/mail/webhook): kallas när ett verktyg nekas av
        # permission engine (t.ex. hårt stopp) — kan dirigera till
        # workspace-approval-kön.
        self.denial_callback = denial_callback

        # Observability: [(tool_name, result_preview), ...] per execution,
        # read by callers (e.g. ai.coworker.run) for session-line persistence
        self.tool_history: list = []

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
        from .tools import TodoList
        self.todo_list = TodoList()
        # Wire up the todo holder so todo_write tool can access it
        from .tools import planning_tools
        planning_tools(self.todo_list)
        # Also register planning tools if not already present
        for pt in planning_tools(self.todo_list):
            if pt.name not in self.tools:
                self.tools.register(pt)

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
        messages.append(Message(role=Role.USER, content=prompt))

        round_num = 0
        total_input_tokens = 0
        total_output_tokens = 0
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
                return ChatResponse(
                    text=f"(cancelled after {round_num} rounds)",
                    input_tokens=total_input_tokens,
                    output_tokens=total_output_tokens,
                    finish_reason="cancelled",
                )

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
                    pass  # Best-effort — never fail a turn over context injection

            # -- Provider call (with cancel support) --
            tool_defs = self.tools.to_openai() if len(self.tools) > 0 else None

            try:
                chat_task = asyncio.create_task(
                    self.provider.chat(
                        model=self.config.model,
                        messages=messages,
                        tools=tool_defs,
                        system_prompt=self.config.system_prompt,
                        temperature=self.config.temperature,
                        max_tokens=self.config.max_tokens,
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
                    return ChatResponse(
                        text="(cancelled)",
                        input_tokens=total_input_tokens,
                        output_tokens=total_output_tokens,
                        finish_reason="cancelled",
                    )

                if chat_task not in done:
                    chat_task.cancel()
                    _logger.error(
                        "LLM call timed out after %.0fs (round %d)",
                        self.config.llm_timeout, round_num,
                    )
                    return ChatResponse(
                        text="Error: LLM call timed out. Please try again.",
                        input_tokens=total_input_tokens,
                        output_tokens=total_output_tokens,
                        finish_reason="timeout",
                    )

                response = chat_task.result()

            except asyncio.CancelledError:
                _logger.info("LLM call cancelled")
                return ChatResponse(
                    text="(cancelled)",
                    input_tokens=total_input_tokens,
                    output_tokens=total_output_tokens,
                    finish_reason="cancelled",
                )
            except Exception as e:
                _logger.error("LLM call failed: %s", e, exc_info=True)
                return ChatResponse(
                    text=f"Error: LLM call failed — {e}",
                    input_tokens=total_input_tokens,
                    output_tokens=total_output_tokens,
                    finish_reason="error",
                )

            total_input_tokens += response.input_tokens
            total_output_tokens += response.output_tokens

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
                return response

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
                                    question=f"Agenten föreslår följande plan:\n\n{plan_text}\n\nGodkänn?",
                                    approval_type="plan_approval",
                                    timeout=300,
                                )
                                if result.get("action") == "answer" and result.get("answer", "").lower().strip() in (
                                    "ja", "yes", "ok", "godkänn", "approve", "kör"
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
                denied_tool_ids: set[str] = set()
                for tc in response.tool_calls:
                    tool = self.tools.get(tc.name)

                    # 1. PermissionEngine.evaluate()
                    decision = self.permissions.evaluate(
                        tc.name, tc.arguments, metadata=tool,
                    )

                    if not decision.allowed:
                        # Engine says no — append error, mark denied
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
                        messages.append(Message(
                            role=Role.TOOL,
                            content=f"Tool '{tc.name}' was denied: {decision.reason}",
                            tool_call_id=tc.id,
                            name=tc.name,
                        ))
                        denied_tool_ids.add(tc.id)
                        continue

                    # 2. If needs_user approval and we have a handler, ask
                    if decision.needs_user and self.interrupt_handler:
                        approved = await self.interrupt_handler.approve_tool(
                            tc.name,
                            tool.risk_level if tool else "read_only",
                            tc.arguments,
                        )
                        if not approved:
                            messages.append(Message(
                                role=Role.TOOL,
                                content=f"Tool '{tc.name}' was denied by user.",
                                tool_call_id=tc.id,
                                name=tc.name,
                            ))
                            denied_tool_ids.add(tc.id)
                            continue
                        # User approved — record as standing rule if applicable
                        self.permissions.allow_tool_for_session(tc.name)

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

                continue  # next round

            # -- Empty response (shouldn't happen) --
            _logger.warning("Empty response from provider — stopping after round %d", round_num)
            return ChatResponse(
                text="(no response)",
                input_tokens=total_input_tokens,
                output_tokens=total_output_tokens,
                finish_reason="error",
            )

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
                    'Sammanfatta nu ditt bästa svar på användarens fråga '
                    'ovan. Svara direkt med slutsatsen/resultatet — inga '
                    'verktyg. Om du saknar tillräcklig data, säg det '
                    'ärligt och ge det du vet.'
                ),
            )]
            synth = await self.provider.chat(
                model=self.config.model,
                messages=synth_messages,
                system_prompt=self.config.system_prompt,
                temperature=self.config.temperature,
                max_tokens=self.config.max_tokens,
            )
            if synth and synth.text:
                return ChatResponse(
                    text=synth.text,
                    input_tokens=total_input_tokens + synth.input_tokens,
                    output_tokens=total_output_tokens + synth.output_tokens,
                    finish_reason="max_rounds",
                )
        except Exception:
            pass
        return ChatResponse(
            text="(max rounds exceeded — stopping)",
            input_tokens=total_input_tokens,
            output_tokens=total_output_tokens,
            finish_reason="max_rounds",
        )

    # -- Tool execution (LOOP-003, LOOP-007) --

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
        """
        tool = self.tools.get(tool_call.name)
        if not tool:
            return f"Error: unknown tool '{tool_call.name}'"

        # NATS executor routing
        if tool.executor == "nats":
            result = await self._execute_via_nats(tool, tool_call.arguments)
            self.tool_history.append((tool_call.name, str(result)[:500]))
            return result

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
                return f"Error: tool '{tool_call.name}' timed out after {self.config.tool_timeout}s"

            result = execute_task.result()
            tool_elapsed = time.time() - tool_start
            self.tool_history.append((tool_call.name, str(result)[:500]))
            _logger.debug(
                "Tool '%s' completed in %.2fs, result length=%d",
                tool_call.name, tool_elapsed, len(result),
            )

        except asyncio.CancelledError:
            return f"Tool '{tool_call.name}' cancelled"
        except Exception as e:
            _logger.warning("Tool '%s' failed: %s", tool_call.name, e)
            return f"Error executing '{tool_call.name}': {e}"

        # Truncate large results
        if len(result) > self.config.max_tool_result_chars:
            half = self.config.max_tool_result_chars // 2
            result = (
                result[:half]
                + f"\n... (truncated {len(result) - self.config.max_tool_result_chars} chars) ...\n"
                + result[-half:]
            )

        return result

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
        """Check if estimated context exceeds the token budget."""
        total_chars = sum(len(m.content or "") for m in messages)
        estimated_tokens = total_chars // 4  # rough: ~4 chars per token
        return estimated_tokens > self.config.max_context_tokens

    async def _summarize(self, messages: list[Message]) -> list[Message]:
        """Summarize conversation history to fit within context budget.

        Buzz pattern: one LLM call to compress, then continue.
        """
        if len(messages) < 4:
            return messages  # nothing to summarize

        # Keep the most recent messages, summarize everything before
        keep_recent = 4
        to_summarize = messages[:-keep_recent]
        recent = messages[-keep_recent:]

        summary_prompt = (
            "Summarize the following conversation. "
            "Keep all key facts, decisions, and context. "
            "Be concise but complete.\n\n"
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
                    system_prompt="You are a summarization assistant. Be concise.",
                    temperature=0.3,
                    max_tokens=2048,
                ),
                timeout=self.config.llm_timeout,
            )
            summary = f"[Previous conversation summary: {response.text}]"
        except Exception as e:
            _logger.warning("Summarization failed: %s — keeping recent messages", e)
            summary = "[Summarization failed — keeping recent context]"

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
        messages.append(Message(role=Role.USER, content=prompt))

        round_num = 0

        while round_num < self.config.max_rounds:
            round_num += 1

            if self._context_too_large(messages):
                messages = await self._summarize(messages)

            tool_defs = self.tools.to_openai() if len(self.tools) > 0 else None

            # Stream from provider
            text_buffer = ""
            round_tokens: list[str] = []
            tool_calls_seen: list[dict] = []

            async for event in self.provider.chat_stream(
                model=self.config.model,
                messages=messages,
                tools=tool_defs,
                system_prompt=self.config.system_prompt,
                temperature=self.config.temperature,
                max_tokens=self.config.max_tokens,
            ):
                if event.type == "token":
                    # Buffra rundans text — först vid done vet vi om det är
                    # narrering (debug) eller slutgiltigt svar (token).
                    text_buffer += event.token
                    round_tokens.append(event.token)

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
                    if tool_calls_seen:
                        # Rundans text var NARRERING (agentens resonemang) —
                        # sänd som debug, inte som svar till användaren.
                        if text_buffer.strip():
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

                        for tc in tool_calls_seen:
                            # SSE-progress for NATS tools
                            tool_obj = self.tools.get(tc["name"])
                            if tool_obj and tool_obj.executor == "nats":
                                yield TokenEvent(
                                    type="tool_progress",
                                    token=json.dumps({
                                        "executor": "nats",
                                        "tool": tc["name"],
                                        "status": "pending",
                                        "message": f"Väntar på Pi-agent ({tc['name']})...",
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
                        # break (inte continue!) — continue skulle bara hämta
                        # nästa event ur samma stream; en dubbel-done från
                        # providern skulle då avsluta loopen innan runda 2.
                        break  # → while-loopen startar nästa runda

                    else:
                        # SLUTGILTIGT SVAR — strömma den buffrade texten
                        for t in round_tokens:
                            yield TokenEvent(type="token", token=t)
                        yield event
                        return

        # max_rounds nådd: gör ett sista anrop UTAN verktyg så användaren
        # alltid får ett sammanfattande svar (tidigare: tomt avslut → klienten
        # visade bara narreringen utan slutsats).
        try:
            async for event in self.provider.chat_stream(
                model=self.config.model,
                messages=messages + [Message(
                    role=Role.USER,
                    content=(
                        'Sammanfatta nu ditt bästa svar på användarens fråga '
                        'ovan. Svara direkt med slutsatsen/resultatet — inga '
                        'verktyg. Om du saknar tillräcklig data, säg det '
                        'ärligt och ge det du vet.'
                    ),
                )],
                system_prompt=self.config.system_prompt,
                temperature=self.config.temperature,
                max_tokens=self.config.max_tokens,
            ):
                if event.type == "token":
                    yield event
                elif event.type == "done":
                    yield event
                    return
        except Exception:
            pass
        yield TokenEvent(type="done", finish_reason="max_rounds")

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
