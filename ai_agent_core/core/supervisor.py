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


class SupervisorLoop:
    """A thin router that selects specialist agents for user prompts.

    Usage:
        router_provider = BifrostProvider()
        specialist = AgentLoop(
            provider=BifrostProvider(),
            tools=analyst_tools,
            config=AgentConfig(model="claude-sonnet-4"),
        )

        supervisor = SupervisorLoop(
            router_provider=router_provider,
            agents=[
                SpecialistAgent(
                    name="analyst",
                    description="Data analysis and reporting. Handles queries about sales, customers, trends.",
                    loop=specialist,
                    triggers=["analyze", "report", "sales", "trend"],
                ),
            ],
        )
        result = await supervisor.run("Analyze sales for Q2")
    """

    def __init__(
        self,
        router_provider: AIProvider,
        agents: list[SpecialistAgent],
        config: Optional[SupervisorConfig] = None,
    ):
        self.router_provider = router_provider
        self.agents: dict[str, SpecialistAgent] = {a.name: a for a in agents}
        self.config = config or SupervisorConfig()

        if not self.agents:
            raise ValueError("At least one specialist agent is required")

        if not self.config.default_agent:
            self.config.default_agent = agents[0].name

    # ── Conference mode ──
    async def conference(
        self,
        prompt: str,
        history: Optional[list[Message]] = None,
        mode: str = "confidence",
    ) -> ChatResponse:
        """Run all agents on same question, select best answer.

        Delegerar till core.ConferenceLoop (change ai-orchestration-tidy-up 6.3).
        mode: 'confidence' (default) | 'majority' | 'synthesis'.
        """
        from odoo.addons.ai_agent_core.core.conference import ConferenceLoop
        loop = ConferenceLoop(
            self.router_provider, list(self.agents.values()),
            self.config, mechanism=mode)
        return await loop.run(prompt, history)

    async def run(
        self,
        prompt: str,
        history: Optional[list[Message]] = None,
    ) -> ChatResponse:
        """Route prompt to specialist agent(s) and return result.

        When an orchestration skill is configured, uses task delegation
        (decompose → delegate → evaluate → refine → synthesize).
        Otherwise uses classic single/multi-agent routing.
        """
        if self.config.skill_recipe:
            return await self.run_task_delegation(prompt, history)
        # Phase 1: Route
        routing = await self._route(prompt)

        # Single agent
        if routing.get("agent"):
            agent_name = routing["agent"]
            agent = self.agents.get(agent_name)
            if not agent:
                _logger.warning(
                    "Router chose unknown agent '%s', falling back to '%s'",
                    agent_name, self.config.default_agent,
                )
                agent = self.agents[self.config.default_agent]

            _logger.info(
                "Supervisor routing: %s → %s (%s)",
                prompt[:80], agent.name, routing.get("reason", ""),
            )
            return await agent.loop.run(prompt, history)

        # Multi-agent (fan-out)
        if routing.get("agents"):
            agent_names = routing["agents"]
            _logger.info(
                "Supervisor fan-out: %s → %s agents (%s)",
                prompt[:80], len(agent_names), routing.get("reason", ""),
            )
            return await self._fan_out(prompt, agent_names, history)

        # Fallback
        _logger.warning("Router returned no agent, falling back to '%s'", self.config.default_agent)
        agent = self.agents[self.config.default_agent]
        return await agent.loop.run(prompt, history)

    # ── Task delegation with iterative refinement ──
    async def run_task_delegation(
        self,
        prompt: str,
        history: Optional[list[Message]] = None,
    ) -> ChatResponse:
        """Decompose → delegate → evaluate → refine → synthesize.

        Phase 1: Delegate — split prompt into sub-tasks, assign specialists.
        Phase 2: Evaluate — check responses for gaps/contradictions.
        Phase 3: Refine — reformulate follow-ups with new context.
        Phase 4: Synthesize — combine into final answer.
        """
        max_iterations = self.config.max_iterations
        min_confidence = self.config.min_confidence
        agent_descriptions = "\n".join(
            f"- **{a.name}**: {a.description}" for a in self.agents.values())

        # Phase 1: Delegate
        delegation = await self._delegate(prompt, agent_descriptions)
        tasks = delegation.get("tasks") or []
        if not tasks:
            # Fallback to single-agent routing
            return await self.run(prompt, history)

        all_results = []  # [(agent_name, task, response, round)]
        current_prompt = prompt

        for round_num in range(1, max_iterations + 1):
            # Run assigned tasks
            round_results = []
            sem = asyncio.Semaphore(self.config.fan_out_concurrency)

            async def run_task(task):
                agent_name = task.get("agent", "")
                task_prompt = task.get("task", current_prompt)
                agent = self.agents.get(agent_name)
                if not agent:
                    return None
                async with sem:
                    resp = await agent.loop.run(task_prompt, history)
                    return agent_name, task_prompt, resp

            results = await asyncio.gather(*[
                run_task(t) for t in tasks if t.get("agent") in self.agents])
            results = [r for r in results if r]

            for name, task_text, resp in results:
                round_results.append((name, task_text, resp))
                all_results.append((name, task_text, resp, round_num))

            # Phase 2: Evaluate — ask router whether gaps remain
            if round_num >= max_iterations:
                break

            eval_result = await self._evaluate(prompt, round_results, agent_descriptions)
            follow_ups = eval_result.get("follow_ups") or []

            # No gaps → stop refining
            if not follow_ups:
                break

            # Phase 3: Refine — build follow-up tasks with context
            tasks = follow_ups

        # Phase 4: Synthesize
        return await self._synthesize(prompt, all_results, agent_descriptions)

    async def _delegate(self, prompt, agent_descriptions):
        """Phase 1: split prompt into sub-tasks assigned to specialists."""
        delegate_prompt = (
            f"Available specialists:\n{agent_descriptions}\n\n"
            f"User request: {prompt}\n\n"
            f"Break this into sub-tasks and assign each to the best specialist. "
            f"Respond with JSON: {{\"tasks\": ["
            f"{{\"agent\": \"<name>\", \"task\": \"<precise sub-task>\", "
            f"\"reason\": \"<why>\"}}]}}"
        )
        try:
            response = await asyncio.wait_for(
                self.router_provider.chat(
                    model=self.config.router_model,
                    messages=[Message(role=Role.USER, content=delegate_prompt)],
                    system_prompt=self._effective_router_prompt(agent_descriptions),
                    temperature=0.1, max_tokens=512,
                ), timeout=30)
            return self._parse_json(response.text)
        except Exception as e:
            _logger.warning("Delegation failed: %s", e)
            return {}

    async def _evaluate(self, original, round_results, agent_descriptions):
        """Phase 2: check responses for gaps and produce follow-ups."""
        outputs = "\n\n".join(
            f"### {name}: {task}\n{resp.text}" for name, task, resp in round_results)
        eval_prompt = (
            f"Original request: {original}\n\n"
            f"Agent responses:\n{outputs}\n\n"
            f"Are there gaps or contradictions? If follow-up needed, "
            f"respond with JSON: {{\"follow_ups\": ["
            f"{{\"agent\": \"<name>\", \"task\": \"<follow-up>\"}}]}} "
            f"If complete, respond: {{\"follow_ups\": []}}"
        )
        try:
            response = await asyncio.wait_for(
                self.router_provider.chat(
                    model=self.config.router_model,
                    messages=[Message(role=Role.USER, content=eval_prompt)],
                    system_prompt="You evaluate agent responses for completeness.",
                    temperature=0.1, max_tokens=512,
                ), timeout=30)
            return self._parse_json(response.text)
        except Exception as e:
            _logger.warning("Evaluation failed: %s", e)
            return {}

    async def _synthesize(self, original, all_results, agent_descriptions):
        """Phase 4: combine all responses into one final answer."""
        outputs = "\n\n".join(
            f"### {name} (round {rnd}): {task}\n{resp.text}"
            for name, task, resp, rnd in all_results)
        synth_prompt = (
            f"Original request: {original}\n\n"
            f"All agent responses:\n{outputs}\n\n"
            f"Synthesize these into ONE cohesive final answer. "
            f"Skip irrelevant parts, resolve contradictions."
        )
        try:
            response = await asyncio.wait_for(
                self.router_provider.chat(
                    model=self.config.router_model,
                    messages=[Message(role=Role.USER, content=synth_prompt)],
                    system_prompt="You synthesize multi-agent responses.",
                    temperature=0.3, max_tokens=2048,
                ), timeout=60)
            total_in = sum(r.input_tokens for _, _, r, _ in all_results) + response.input_tokens
            total_out = sum(r.output_tokens for _, _, r, _ in all_results) + response.output_tokens
            return ChatResponse(text=response.text,
                input_tokens=total_in, output_tokens=total_out,
                finish_reason="stop")
        except Exception as e:
            _logger.warning("Synthesis failed: %s — returning concatenated", e)
            merged = "\n\n".join(
                f"## {name}\n{resp.text}" for name, _, resp, _ in all_results)
            total_in = sum(r.input_tokens for _, _, r, _ in all_results)
            total_out = sum(r.output_tokens for _, _, r, _ in all_results)
            return ChatResponse(text=merged,
                input_tokens=total_in, output_tokens=total_out,
                finish_reason="stop")

    @staticmethod
    def _parse_json(text):
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

    async def run_stream(self, prompt: str, history: Optional[list[Message]] = None):
        """Streaming variant — routes to one agent and streams."""
        routing = await self._route(prompt)
        agent_name = routing.get("agent", self.config.default_agent)
        agent = self.agents.get(agent_name, self.agents[self.config.default_agent])

        if isinstance(agent.loop, StreamingAgentLoop):
            async for event in agent.loop.run_stream(prompt, history):
                yield event
        else:
            # Fallback: non-streaming agent wrapped as token
            result = await agent.loop.run(prompt, history)
            yield TokenEvent(type="token", token=result.text)
            yield TokenEvent(type="done", finish_reason=result.finish_reason)

    def _effective_router_prompt(self, agent_descriptions):
        """Return router system prompt — from skill recipe or default."""
        if self.config.skill_recipe:
            return (
                f"{self.config.skill_recipe}\n\n"
                f"Available specialists:\n{agent_descriptions}\n\n"
                f"Respond with JSON only."
            )
        return self.config.router_system_prompt

    async def _route(self, prompt: str) -> dict:
        """Ask the router LLM which agent(s) should handle this prompt.

        Delar router-logik med buzz via core.router.LLMRouter
        (change ai-orchestration-tidy-up 6.4). Fallback: nyckelords-matchning.
        """
        from odoo.addons.ai_agent_core.core.router import LLMRouter
        router = LLMRouter(self.router_provider, self.config.router_model)
        agents = [
            {'name': a.name, 'description': a.description,
             'triggers': a.triggers}
            for a in self.agents.values()
        ]
        agent_descriptions = "\n".join(
            f"- **{a.name}**: {a.description}" for a in self.agents.values())
        return await router.route(
            prompt, agents,
            system_prompt=self._effective_router_prompt(agent_descriptions),
        )

    async def _fan_out(
        self,
        prompt: str,
        agent_names: list[str],
        history: Optional[list[Message]] = None,
    ) -> ChatResponse:
        """Run multiple agents in parallel and merge results."""
        tasks = []
        valid_names = []

        for name in agent_names:
            if name in self.agents:
                valid_names.append(name)
                tasks.append(self.agents[name].loop.run(prompt, history))
            else:
                _logger.warning("Fan-out: unknown agent '%s', skipping", name)

        if not tasks:
            agent = self.agents[self.config.default_agent]
            return await agent.loop.run(prompt, history)

        # Run with concurrency limit
        semaphore = asyncio.Semaphore(self.config.fan_out_concurrency)

        async def bounded_run(agent_name, coro):
            async with semaphore:
                _logger.debug("Fan-out: running agent '%s'", agent_name)
                return agent_name, await coro

        results = await asyncio.gather(*[
            bounded_run(name, task)
            for name, task in zip(valid_names, tasks)
        ])

        if len(results) == 1:
            return results[0][1]

        # Merge results
        if self.config.merge_multi_agent:
            return await self._merge_results(prompt, results)
        else:
            # Return concatenated
            merged_text = "\n\n".join(
                f"## {name}\n{resp.text}"
                for name, resp in results
            )
            total_in = sum(r.input_tokens for _, r in results)
            total_out = sum(r.output_tokens for _, r in results)
            return ChatResponse(
                text=merged_text,
                input_tokens=total_in,
                output_tokens=total_out,
                finish_reason="stop",
            )

    async def _merge_results(
        self,
        prompt: str,
        results: list[tuple[str, ChatResponse]],
    ) -> ChatResponse:
        """Summarize results from multiple agents into one response."""
        agent_outputs = "\n\n".join(
            f"### Agent: {name}\n{resp.text}"
            for name, resp in results
        )

        merge_prompt = (
            f"Original user request: {prompt}\n\n"
            f"Multiple agents provided responses:\n{agent_outputs}\n\n"
            f"Synthesize these into ONE cohesive response. "
            f"Remove contradictions, merge overlapping information, "
            f"and present a unified answer."
        )

        try:
            response = await self.router_provider.chat(
                model=self.config.router_model,
                messages=[Message(role=Role.USER, content=merge_prompt)],
                system_prompt="You are a synthesis assistant. Merge multiple responses into one.",
                temperature=0.3,
                max_tokens=2048,
            )
            total_in = sum(r.input_tokens for _, r in results) + response.input_tokens
            total_out = sum(r.output_tokens for _, r in results) + response.output_tokens
            return ChatResponse(
                text=response.text,
                input_tokens=total_in,
                output_tokens=total_out,
                finish_reason="stop",
            )
        except Exception as e:
            _logger.warning("Merge failed: %s — returning concatenated", e)
            merged_text = "\n\n".join(
                f"## {name}\n{resp.text}"
                for name, resp in results
            )
            total_in = sum(r.input_tokens for _, r in results)
            total_out = sum(r.output_tokens for _, r in results)
            return ChatResponse(
                text=merged_text,
                input_tokens=total_in,
                output_tokens=total_out,
                finish_reason="stop",
            )


class StreamingSupervisorLoop(SupervisorLoop):
    """Supervisor with streaming support for single-agent routing."""

    async def run_stream(self, prompt: str, history: Optional[list[Message]] = None):
        routing = await self._route(prompt)
        agent_name = routing.get("agent", self.config.default_agent)
        agent = self.agents.get(agent_name, self.agents[self.config.default_agent])

        yield TokenEvent(type="token", token=f"[{agent.name}] ")
        if isinstance(agent.loop, StreamingAgentLoop):
            async for event in agent.loop.run_stream(prompt, history):
                yield event
        else:
            result = await agent.loop.run(prompt, history)
            yield TokenEvent(type="token", token=result.text)
            yield TokenEvent(type="done", finish_reason=result.finish_reason)
