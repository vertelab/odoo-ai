# -*- coding: utf-8 -*-
"""
Context management — Auto-summarize + token estimation (LOOP-004).

When context exceeds the model's window, summarize history and continue.
Buzz-validated pattern: one LLM call to compress.
"""

import logging

from .provider import AIProvider, Message, Role

_logger = logging.getLogger(__name__)


def estimate_tokens(messages: list[Message]) -> int:
    """Rough token estimate: ~4 characters per token."""
    total = sum(len(m.content or "") for m in messages)
    return total // 4


def is_context_full(messages: list[Message], max_tokens: int = 128_000) -> bool:
    """Check if context exceeds the token budget."""
    return estimate_tokens(messages) > max_tokens


async def summarize_history(
    provider: AIProvider,
    model: str,
    messages: list[Message],
    keep_recent: int = 4,
) -> list[Message]:
    """Summarize conversation history to fit within context budget.

    Keeps the most recent messages intact.
    Summarizes everything before them into a system message.

    Buzz pattern: one LLM call, then continue.
    """
    if len(messages) <= keep_recent:
        return messages

    to_summarize = messages[:-keep_recent]
    recent = messages[-keep_recent:]

    summary_text = "\n".join(
        f"{m.role.value}: {m.content[:500]}" for m in to_summarize if m.content
    )

    if not summary_text.strip():
        return recent  # nothing to summarize

    summary_prompt = (
        "Summarize the following conversation. "
        "Keep all key facts, decisions, numbers, and context. "
        "Be concise but complete.\n\n"
        + summary_text
    )

    try:
        response = await provider.chat(
            model=model,
            messages=[Message(role=Role.USER, content=summary_prompt)],
            system_prompt="You are a summarization assistant. Be concise.",
            temperature=0.3,
            max_tokens=2048,
        )
        summary = f"[Previous conversation summary: {response.text}]"
    except Exception as e:
        _logger.warning("Summarization failed: %s", e)
        summary = "[Summarization failed — keeping recent context]"

    return [Message(role=Role.SYSTEM, content=summary)] + recent
