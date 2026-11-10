# -*- coding: utf-8 -*-
from __future__ import annotations
"""
Supervisor Loop — multi-agent orchestration (PAPER-001).

A thin router that selects a specialist agent for each user prompt.
Inspired by Buzz's Router pattern and Paperclip's agent team model.

The SupervisorLoop:
1. Routes the user prompt to one or more specialist agents
2. Runs each agent's loop sequentially (or in parallel for fan-out)
3. Optionally summarizes results from multiple agents

No LangGraph. No StateGraph. Just a router LLM call + agent dispatch.
"""

import asyncio
import json
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from .loop import AgentLoop, AgentConfig, StreamingAgentLoop
from .provider import AIProvider, ChatResponse, Message, Role, TokenEvent, ToolCall
from .tools import Tool, ToolRegistry


def _usage_of(results):
    """Samla per-agent token & modell (Väg A) ur fan-out (name, ChatResponse)."""
    return [{
        'agent': str(name or ''),
        'model': resp.model or '',
        'input_tokens': int(getattr(resp, 'input_tokens', 0) or 0),
        'output_tokens': int(getattr(resp, 'output_tokens', 0) or 0),
    } for name, resp in results if resp]

_logger = logging.getLogger(__name__)


@dataclass
class SupervisorConfig:
    """Configuration for a SupervisorLoop."""
    # Orchestration skill recipe (overrides defaults when provided)
    skill_recipe: str = ""                # recipe_text from orchestration.supervisor skill
    max_rounds: int = 3                    # Max routing attempts before fallback
    default_agent: str = ""                # Fallback agent if routing fails
    merge_multi_agent: bool = True         # Summarize results from multiple agents
    fan_out_concurrency: int = 3           # Max parallel agents in fan-out mode
    max_iterations: int = 3                # Max refinement rounds in task delegation
    min_confidence: float = 0.8            # Confidence threshold for evaluation
    router_model: str = ""                 # Model för routing ('' → caller/provider)
    router_system_prompt: str = (          # How the router selects agents
        "You are a routing assistant. Given a user request and a list of available agents, "
        "choose the best agent to handle the request. Respond with ONLY a JSON object: "
        '{"agent": "<name>", "reason": "<brief explanation>"} or '
        '{"agents": ["<name1>", "<name2>"], "reason": "<explanation>"} for multi-agent tasks.'
    )


@dataclass
class SpecialistAgent:
    """A named agent loop with metadata for routing."""
    name: str
    description: str                       # What this agent does (for router LLM)
    loop: AgentLoop
    triggers: list[str] = field(default_factory=list)  # Keywords that suggest this agent


# ─────────────────────────────────────────────────────────────────────
# SupervisorLoop och StreamingSupervisorLoop är PENSIONERADE
# (wire-orphaned-core-mechanisms §7, 2026-11-02).
#
# Beteendet bor nu i skill-recipen `orchestration.supervisor`: en vanlig
# AgentLoop (eller StreamingAgentLoop) med specialisternas verktyg
# registrerade. Det ger EN körväg, och därmed samma HITL-grind som
# single-läget — vilket den hårdkodade routern aldrig hade.
#
# `SupervisorConfig` och `SpecialistAgent` finns kvar: ConferenceLoop
# importerar dem.
# ─────────────────────────────────────────────────────────────────────
