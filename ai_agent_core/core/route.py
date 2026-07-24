# -*- coding: utf-8 -*-
from __future__ import annotations
"""
Intelligent Router — path selection: existing → local → remote (TASK-002).

When a user requests a new quest/action, the system MUST decide:
- `existing`: a quest already covers this → use it
- `local`: can be solved with current tools/configuration → do it locally
- `remote`: needs external LLM generation → call provider (costs tokens)

Agent MUST write its reasoning BEFORE naming the destination.
Local-first bias: don't spend API calls unnecessarily.
"""

import json
import logging
from dataclasses import dataclass, field
from typing import Optional

_logger = logging.getLogger(__name__)


@dataclass
class RouteDecision:
    """Routing decision with reasoning."""
    destination: str           # existing | local | remote
    reasoning: str             # WHY this destination was chosen
    confidence: float = 0.0    # 0.0 - 1.0
    quest_id: int = 0          # If destination=existing, which quest?
    tool_names: list[str] = field(default_factory=list)  # If local, which tools?
    model_suggestion: str = ""  # If remote, suggested model
    estimated_cost: float = 0.0  # If remote, estimated token cost

    def to_json(self) -> str:
        return json.dumps({
            "destination": self.destination,
            "reasoning": self.reasoning,
            "confidence": self.confidence,
            "quest_id": self.quest_id,
            "tool_names": self.tool_names,
            "model_suggestion": self.model_suggestion,
            "estimated_cost": self.estimated_cost,
        }, indent=2)


class IntelligentRouter:
    """Routes user prompts to the optimal execution path.

    Local-first bias: prefer existing quests and local tools over
    expensive LLM calls.

    Usage:
        router = IntelligentRouter()
        detector = EnvironmentDetector()
        env_info = detector.scan_full(env)

        decision = router.route(
            prompt="Analyze Q2 sales",
            env_info=env_info,
        )
        print(decision.destination)  # "existing", "local", or "remote"
    """

    def __init__(self, cost_per_1k_input: float = 0.003,
                 cost_per_1k_output: float = 0.015):
        self.cost_per_1k_input = cost_per_1k_input
        self.cost_per_1k_output = cost_per_1k_output

    def route(
        self,
        prompt: str,
        env_info=None,
        existing_quests: Optional[list[dict]] = None,
        available_tools: Optional[list[str]] = None,
    ) -> RouteDecision:
        """Route a user prompt to the optimal execution path.

        Args:
            prompt: The user's request
            env_info: DetectResult from EnvironmentDetector.scan_full()
            existing_quests: List of existing quest dicts (name, description, status)
            available_tools: List of available tool names

        Returns:
            RouteDecision with destination and reasoning
        """
        prompt_lower = prompt.lower()

        # -- Step 1: Check existing quests --
        if existing_quests or (env_info and env_info.existing_quests):
            quests = existing_quests or [
                {"name": q.name, "description": q.description, "status": q.status}
                for q in env_info.existing_quests
            ]
            match = self._match_existing_quest(prompt, prompt_lower, quests)
            if match:
                return match

        # -- Step 2: Check if local tools can handle it --
        if available_tools or (env_info and env_info.registered_models):
            local = self._check_local_capability(
                prompt, prompt_lower, available_tools, env_info
            )
            if local:
                return local

        # -- Step 3: Needs remote LLM --
        return self._route_remote(prompt, env_info)

    def _match_existing_quest(
        self,
        prompt: str,
        prompt_lower: str,
        quests: list[dict],
    ) -> Optional[RouteDecision]:
        """Check if an existing quest covers this prompt."""
        best_match = None
        best_score = 0
        best_quest = None

        for q in quests:
            if q.get("status") in ("error", "draft") and q.get("status") != "active":
                continue  # Skip non-active quests

            score = 0
            q_name = (q.get("name", "") or "").lower()
            q_desc = (q.get("description", "") or "").lower()

            # Keyword overlap
            prompt_words = set(prompt_lower.split())
            name_words = set(q_name.split())
            desc_words = set(q_desc.split())

            name_overlap = prompt_words & name_words
            desc_overlap = prompt_words & desc_words

            score += len(name_overlap) * 5
            score += len(desc_overlap) * 2

            # Exact substring in name
            if len(q_name) > 3 and q_name in prompt_lower:
                score += 15

            if score > best_score:
                best_score = score
                best_quest = q

        # Threshold: need decent overlap
        if best_score >= 8 and best_quest:
            return RouteDecision(
                destination="existing",
                reasoning=(
                    f"Existing quest '{best_quest.get('name')}' matches prompt "
                    f"(score={best_score}). Keywords overlap. "
                    f"Reusing existing quest avoids duplicate work."
                ),
                confidence=min(best_score / 30, 1.0),
                quest_id=best_quest.get("id", 0),
            )

        return None

    def _check_local_capability(
        self,
        prompt: str,
        prompt_lower: str,
        available_tools: Optional[list[str]],
        env_info,
    ) -> Optional[RouteDecision]:
        """Check if the prompt can be handled without an LLM call."""
        tools = available_tools or []

        # If env_info has models, we can try direct queries
        has_models = env_info and env_info.registered_models

        # Pattern: direct data request
        data_patterns = [
            ("show me", "search_read"),
            ("list", "search_read"),
            ("find", "search_read"),
            ("count", "search_read"),
            ("how many", "search_read"),
            ("what is the", "search_read"),
        ]

        for pattern, tool_type in data_patterns:
            if pattern in prompt_lower:
                # Look for model names in prompt
                if env_info and env_info.registered_models:
                    for model in env_info.registered_models[:30]:
                        model_lower = model.display_name.lower()
                        if model_lower in prompt_lower or model.name in prompt_lower:
                            tool_name = f"search_read_{model.name.replace('.', '_')}"
                            return RouteDecision(
                                destination="local",
                                reasoning=(
                                    f"Prompt matches data query pattern '{pattern}' "
                                    f"on model '{model.name}'. "
                                    f"Can be handled locally with tool '{tool_name}' "
                                    f"without LLM cost."
                                ),
                                confidence=0.85,
                                tool_names=[tool_name],
                            )

        # Check if tool names match
        for tool in tools:
            tool_lower = tool.lower()
            if tool_lower in prompt_lower:
                return RouteDecision(
                    destination="local",
                    reasoning=(
                        f"Prompt mentions tool '{tool}' directly. "
                        f"Can execute locally without LLM routing."
                    ),
                    confidence=0.9,
                    tool_names=[tool],
                )
            # Check tool parts
            parts = tool_lower.split("_")
            if any(p in prompt_lower and len(p) > 3 for p in parts):
                return RouteDecision(
                    destination="local",
                    reasoning=(
                        f"Prompt keywords match tool '{tool}'. "
                        f"Local execution preferred."
                    ),
                    confidence=0.7,
                    tool_names=[tool],
                )

        return None

    def _route_remote(self, prompt: str, env_info) -> RouteDecision:
        """Route to remote LLM — estimate cost and suggest model."""
        estimated_input_tokens = len(prompt) // 4 + 200  # + system prompt
        estimated_output_tokens = 500  # typical response

        cost = (
            estimated_input_tokens / 1000 * self.cost_per_1k_input
            + estimated_output_tokens / 1000 * self.cost_per_1k_output
        )

        # Pick cheapest capable model from available
        model = "cerebras/gpt-oss-120b"  # default
        if env_info and env_info.available_models:
            # Prefer cheapest model
            models = sorted(
                env_info.available_models,
                key=lambda m: (
                    m.capabilities.get("context_window", 0) if isinstance(m.capabilities, dict)
                    else 0
                ),
                reverse=True,
            )
            if models:
                model = models[0].model_id

        return RouteDecision(
            destination="remote",
            reasoning=(
                f"No existing quest matches and no local tool can handle this. "
                f"Estimated input: ~{estimated_input_tokens} tokens, "
                f"output: ~{estimated_output_tokens} tokens. "
                f"Estimated cost: ${cost:.4f}. "
                f"Using model: {model}."
            ),
            confidence=0.95,
            model_suggestion=model,
            estimated_cost=cost,
        )


# ---------------------------------------------------------------------------
# Rule-based router (no LLM needed)
# ---------------------------------------------------------------------------

class RuleBasedRouter(IntelligentRouter):
    """Router that only uses keyword matching — no LLM routing cost.

    Best for quick, deterministic routing when LLM cost is a concern.
    """

    def route(
        self,
        prompt: str,
        env_info=None,
        existing_quests=None,
        available_tools=None,
    ) -> RouteDecision:
        prompt_lower = prompt.lower()

        # Existing quests always checked first
        if existing_quests or (env_info and env_info.existing_quests):
            quests = existing_quests or [
                {"name": q.name, "description": q.description, "status": q.status,
                 "id": q.id}
                for q in env_info.existing_quests
            ]
            match = self._match_existing_quest(prompt, prompt_lower, quests)
            if match:
                return match

        # Local check
        local = self._check_local_capability(
            prompt, prompt_lower, available_tools, env_info
        )
        if local:
            return local

        # Everything else → remote
        return self._route_remote(prompt, env_info)
