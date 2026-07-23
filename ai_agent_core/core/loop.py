# -*- coding: utf-8 -*-
from __future__ import annotations
"""
Agent Loop — Buzz-inspired while-loop (LOOP-001, LOOP-003).

LOOP-001: prompt → provider.chat() → execute tools → repeat
LOOP-003: Sequential tool execution, bounded by tool_timeout.
           Errors return strings, never crash the loop.

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


# ---------------------------------------------------------------------------
# AgentLoop (LOOP-001)
# ---------------------------------------------------------------------------


class AgentLoop:
    """A Buzz-inspired agent loop.

    Usage:
        provider = BifrostProvider()
        tools = ToolRegistry()
        tools.register_many(builtin_tools())

        loop = AgentLoop(provider=provider, tools=tools, config=AgentConfig())
        result = await loop.run("What is 2+2?")
    """

    def __init__(
        self,
        provider: AIProvider,
        tools: ToolRegistry,
        config: Optional[AgentConfig] = None,
    ):
        self.provider = provider
        self.tools = tools
        self.config = config or AgentConfig()

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

        while round_num < self.config.max_rounds:
            round_num += 1
            _logger.debug(
                "AgentLoop round %d/%d — %d messages, %d tools",
                round_num, self.config.max_rounds,
                len(messages), len(self.tools),
            )

            # -- Context management --
            if self._context_too_large(messages):
                _logger.info("Context too large — summarizing")
                messages = await self._summarize(messages)

            # -- Provider call --
            tool_defs = self.tools.to_openai() if len(self.tools) > 0 else None

            try:
                response = await asyncio.wait_for(
                    self.provider.chat(
                        model=self.config.model,
                        messages=messages,
                        tools=tool_defs,
                        system_prompt=self.config.system_prompt,
                        temperature=self.config.temperature,
                        max_tokens=self.config.max_tokens,
                    ),
                    timeout=self.config.llm_timeout,
                )
            except asyncio.TimeoutError:
                _logger.error("LLM call timed out after %.0fs", self.config.llm_timeout)
                return ChatResponse(
                    text="Error: LLM call timed out. Please try again.",
                    finish_reason="timeout",
                )

            total_input_tokens += response.input_tokens
            total_output_tokens += response.output_tokens

            # -- Text response (no tool calls) → done --
            if response.text and not response.tool_calls:
                response.input_tokens = total_input_tokens
                response.output_tokens = total_output_tokens
                return response

            # -- Tool calls → execute --
            if response.tool_calls:
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

                # Execute tools sequentially (LOOP-003)
                for tc in response.tool_calls:
                    result = await self._execute_tool(tc)
                    messages.append(Message(
                        role=Role.TOOL,
                        content=result,
                        tool_call_id=tc.id,
                        name=tc.name,
                    ))

                continue  # next round

            # -- Empty response (shouldn't happen) --
            _logger.warning("Empty response from provider — stopping")
            return ChatResponse(
                text="(no response)",
                input_tokens=total_input_tokens,
                output_tokens=total_output_tokens,
                finish_reason="error",
            )

        # -- Max rounds exceeded --
        _logger.warning("Max rounds (%d) exceeded", self.config.max_rounds)
        return ChatResponse(
            text="(max rounds exceeded — stopping)",
            input_tokens=total_input_tokens,
            output_tokens=total_output_tokens,
            finish_reason="max_rounds",
        )

    # -- Tool execution (LOOP-003) --

    async def _execute_tool(self, tool_call) -> str:
        """Execute a single tool call with timeout and error handling."""
        tool = self.tools.get(tool_call.name)
        if not tool:
            return f"Error: unknown tool '{tool_call.name}'"

        _logger.debug("Executing tool: %s", tool_call.name)

        try:
            result = await asyncio.wait_for(
                tool.execute(**tool_call.arguments),
                timeout=self.config.tool_timeout,
            )
        except asyncio.TimeoutError:
            return f"Error: tool '{tool_call.name}' timed out after {self.config.tool_timeout}s"
        except Exception as e:
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
