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
from .tools import Tool, ToolRegistry

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
    tool_timeout: float = 30.0  # seconds per tool execution
    llm_timeout: float = 120.0  # seconds per LLM call
    max_tool_result_chars: int = 8000  # truncate large tool results
    max_parallel_tools: int = 5  # max concurrent tool executions (LOOP-007)
    approval_threshold: int = 2  # risk level requiring approval (HITL-005)
    max_clarifications: int = 3  # max proactive questions per turn (HITL-007)


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
        provider = BifrostProvider()
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
    ):
        self.provider = provider
        self.tools = tools
        self.config = config or AgentConfig()
        self.interrupt_handler = interrupt_handler

        # Cancellation support (LOOP-005)
        self._cancel_event = asyncio.Event()
        self._cancelled = False
        self._partial_results: list[tuple[str, str]] = []

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

                # -- Approval check (HITL-005) --
                if self.interrupt_handler:
                    for tc in response.tool_calls:
                        tool = self.tools.get(tc.name)
                        if tool and tool.needs_human_approval(self.config.approval_threshold):
                            approved = await self.interrupt_handler.approve_tool(
                                tc.name, tool.risk_level, tc.arguments
                            )
                            if not approved:
                                messages.append(Message(
                                    role=Role.TOOL,
                                    content=f"Tool '{tc.name}' was denied by user.",
                                    tool_call_id=tc.id,
                                    name=tc.name,
                                ))
                                # Remove denied tool calls from this batch
                                response.tool_calls = [
                                    t for t in response.tool_calls
                                    if t.id != tc.id
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
        """Execute a single tool call with timeout and error handling."""
        tool = self.tools.get(tool_call.name)
        if not tool:
            return f"Error: unknown tool '{tool_call.name}'"

        tool_start = time.time()
        _logger.debug("Executing tool: %s", tool_call.name)

        try:
            execute_task = asyncio.create_task(
                tool.execute(**tool_call.arguments)
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
                    text_buffer += event.token
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
                    if tool_calls_seen:
                        # Execute tools
                        assistant_msg = Message(
                            role=Role.ASSISTANT,
                            content=text_buffer,
                            tool_calls=[
                                {
                                    "id": tc["id"],
                                    "type": "function",
                                    "function": {
                                        "name": tc["name"],
                                        "arguments": str(tc["arguments"]),
                                    },
                                }
                                for tc in tool_calls_seen
                            ],
                        )
                        messages.append(assistant_msg)

                        for tc in tool_calls_seen:
                            result = await self._execute_tool(
                                ToolCall(id=tc["id"], name=tc["name"], arguments=tc["arguments"])
                            )
                            messages.append(Message(
                                role=Role.TOOL,
                                content=result,
                                tool_call_id=tc["id"],
                                name=tc["name"],
                            ))

                        tool_calls_seen = []
                        text_buffer = ""
                        continue  # next round

                    else:
                        # Done — return final text
                        yield event
                        return
