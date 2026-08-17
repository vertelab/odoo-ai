# -*- coding: utf-8 -*-
"""LinearLoop — sequential pipeline orchestration."""

import asyncio
import logging
from dataclasses import dataclass

from .loop import AgentLoop, AgentConfig, ChatResponse

_logger = logging.getLogger(__name__)


@dataclass
class LinearConfig:
    """Configuration for LinearLoop."""
    pass_full_history: bool = False


class LinearLoop:
    """Sequential pipeline: agents execute in order, each output feeds next.

    Usage:
        loop = LinearLoop(agents=[...], provider=provider, tools=tools)
        result = await loop.run("Create a report")
    """

    def __init__(
        self,
        agents,
        provider,
        tools,
        base_model="gpt-4o",
        base_system="",
        max_rounds=10,
        config=None,
    ):
        self.agents = agents  # ai.coworker.agent records sorted by sequence
        self.provider = provider
        self.tools = tools
        self.base_model = base_model
        self.base_system = base_system
        self.max_rounds = max_rounds
        self.config = config or LinearConfig()

    async def run(self, prompt, history=None):
        """Run the pipeline: each agent gets the previous output as prompt."""
        current_prompt = prompt
        total_in = 0
        total_out = 0
        agent_results = []

        for agent_rel in self.agents:
            agent = agent_rel.agent_id
            if not agent:
                continue

            agent_model = agent.model_id._get_api_name() if agent and agent.model_id else self.base_model
            agent_system = self.base_system
            if agent:
                agent_system = (
                    f"Role: {agent.ai_role or agent.name}\n"
                    f"Goal: {agent.ai_goal or ''}\n"
                    f"{self.base_system}"
                )

            loop = AgentLoop(
                provider=self.provider,
                tools=self.tools,
                config=AgentConfig(
                    model=agent_model,
                    system_prompt=agent_system,
                    max_rounds=self.max_rounds,
                ),
            )

            # Pass context from previous steps
            if self.config.pass_full_history and agent_results:
                ctx = "\n\n".join(
                    f"## Previous step: {r['agent_name']}\n{r['output']}"
                    for r in agent_results
                )
                step_prompt = f"{ctx}\n\n## Current task\n{current_prompt}"
            else:
                step_prompt = current_prompt

            _logger.info("Linear: running agent '%s' (model=%s)", agent.name, agent_model)
            result = await loop.run(step_prompt, history)
            current_prompt = result.text or ""
            total_in += result.input_tokens or 0
            total_out += result.output_tokens or 0
            agent_results.append({
                'agent_name': agent.name,
                'output': current_prompt,
                'tokens_in': result.input_tokens,
                'tokens_out': result.output_tokens,
            })

        return ChatResponse(
            text=current_prompt,
            input_tokens=total_in,
            output_tokens=total_out,
            finish_reason="stop",
        )
