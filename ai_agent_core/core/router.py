# -*- coding: utf-8 -*-
from __future__ import annotations
"""
LLM Agent Router — gemensam agent-selektering för buzz + supervisor
(change ai-orchestration-tidy-up, orchestration-code-unification 6.4).

Ersätter de två parallella router-implementationerna:
  - SupervisorLoop._route()
  - _buzz_llm_route()

Samma prompt-mönster, samma JSON-kontrakt, samma modell-resolvering
(coworkerns modell — aldrig hårdkodad modell-id).
"""

import asyncio
import json
import logging
from typing import Optional

from .provider import Message, Role

_logger = logging.getLogger(__name__)


class LLMRouter:
    """Ask a router LLM which agent(s) should handle a prompt.

    Usage:
        router = LLMRouter(provider, model="claude-sonnet-4")
        decision = await router.route(
            prompt="Analyze Q2 sales",
            agents=[
                {"name": "analyst", "description": "Sales analysis",
                 "triggers": ["sales", "trend"]},
            ],
        )
        # decision == {"agent": "analyst", "reason": "..."} or
        #            {"agents": ["analyst", ...], "reason": "..."}
    """

    def __init__(self, provider, model: str = ""):
        self.provider = provider
        self.model = model or ""   # '' → provider default (caller resolves)

    @staticmethod
    def build_prompt(prompt: str, agents: list[dict]) -> str:
        """Gemensamt prompt-mönster (buzz + supervisor)."""
        agent_descriptions = "\n".join(
            f"- **{a['name']}**: {a.get('description', '')}"
            + (f" Triggers: {', '.join(a.get('triggers') or [])}"
               if a.get('triggers') else "")
            for a in agents
        )
        return (
            f"Available agents:\n{agent_descriptions}\n\n"
            f"User request: {prompt}\n\n"
            f"Which agent(s) should handle this? Respond with JSON only."
        )

    @staticmethod
    def parse_json(text: str) -> dict:
        """Extract JSON from LLM response (handles markdown fences)."""
        text = (text or "").strip()
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        try:
            return json.loads(text.strip())
        except json.JSONDecodeError:
            return {}

    @staticmethod
    def keyword_route(prompt: str, agents: list[dict],
                      default_agent: str = "") -> dict:
        """Fallback: match trigger keywords."""
        prompt_lower = (prompt or "").lower()
        best = default_agent
        best_score = 0
        for a in agents:
            score = sum(
                1 for kw in (a.get('triggers') or [])
                if kw and kw.lower() in prompt_lower
            )
            if score > best_score:
                best_score = score
                best = a['name']
        return {"agent": best, "reason": "keyword match"}

    async def route(
        self,
        prompt: str,
        agents: list[dict],
        system_prompt: Optional[str] = None,
        max_tokens: int = 256,
        timeout: float = 30.0,
    ) -> dict:
        """Ask the router LLM which agent(s) should handle the prompt.

        Args:
            prompt: User request text
            agents: list of dicts {name, description, triggers}
            system_prompt: Optional router system prompt override
            max_tokens: Router response cap
            timeout: Seconds before falling back to keyword matching

        Returns:
            dict: {"agent": name} or {"agents": [names]} or {} on failure
        """
        router_prompt = self.build_prompt(prompt, agents)
        from odoo.addons.ai_agent_core.core.provider import get_default_model_name
        model = self.model or get_default_model_name()
        try:
            response = await asyncio.wait_for(
                self.provider.chat(
                    model=model,
                    messages=[Message(role=Role.USER, content=router_prompt)],
                    system_prompt=system_prompt,
                    temperature=0.1,
                    max_tokens=max_tokens,
                ),
                timeout=timeout,
            )
            return self.parse_json(getattr(response, 'text', ''))
        except Exception as e:
            _logger.warning("LLM router failed (%s) — keyword fallback", e)
            default = agents[0]['name'] if agents else ''
            return self.keyword_route(prompt, agents, default)
