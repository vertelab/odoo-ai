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
        session_headers=None,
        outcome_callback=None,
        checkpoint_callback=None,
        interrupt_handler=None,
    ):
        self.agents = agents  # ai.coworker.agent records sorted by sequence
        self.provider = provider
        self.tools = tools
        self.base_model = base_model
        self.base_system = base_system
        self.max_rounds = max_rounds
        self.config = config or LinearConfig()
        # Bifrost-sessionskorrelation (bifrost-session-lankning): samma
        # per-anrops-headers till varje steg i kedjan.
        self.session_headers = session_headers
        # Utfalls-persistens och transaktionshygien (ai-agent-core-fault-
        # remediation A1/B3): `_build_loop` skickar båda hit, precis som till
        # AgentLoop. Utan dem tappade linear-vägen utfallsmätningen OCH höll
        # ACCESS SHARE-lås öppna över provider-anropen — samma buggklass som
        # checkpoint-commit-fixen 2026-10-08 botade för de andra vägarna.
        #
        # Loopen förblir domän-ren: den anropar bara callbacks, äger ingen
        # skrivning. Samma mönster som AgentConfig.
        self.outcome_callback = outcome_callback
        self.checkpoint_callback = checkpoint_callback
        # HITL-kanalen (init-type-execution-parity 2.1): varje steg i
        # pipelinen är en egen AgentLoop — utan den här skulle ett
        # `needs_user`-beslut i steg 2 nekas tyst medan steg 1 pausade.
        self.interrupt_handler = interrupt_handler

    def _checkpoint(self, label: str):
        """Släpp transaktionen mellan stegen (samma kontrakt som AgentLoop).

        Anropas före varje provider-anrop i kedjan. Får aldrig kasta vidare:
        en misslyckad commit ska inte fälla en pipeline som annars fungerar.
        """
        cb = self.checkpoint_callback
        if cb:
            try:
                cb(label)
            except Exception:
                _logger.exception(
                    'checkpoint_callback misslyckades (%s)', label)

    def _finish(self, response):
        """Anropa outcome_callbacken (om satt) och returnera svaret oförändrat.

        Samma kontrakt som `AgentLoop._finish`: varje returväg ur `run()` går
        genom denna, så utfallet persisteras oavsett hur pipelinen slutade.
        Stegen har sin EGEN AgentLoop som redan anropar sin callback; denna
        täcker pipelinesvaret (det som ägaren faktiskt får tillbaka).
        """
        cb = self.outcome_callback
        if cb:
            try:
                cb(response)
            except Exception:
                _logger.exception('outcome_callback misslyckades')
        return response

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

            # Transaktionshygien: släpp låsen innan vi väntar på providern.
            # Steget ovan kan ha kört verktyg som läste tabeller; en lång
            # LLM-väntan med öppen transaktion blockerar moduluppgraderingar
            # (mätt 35–39 s idle-in-transaction, T/50690).
            self._checkpoint('round-start')

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
                    session_headers=self.session_headers,
                    # Stegets loop bär SAMMA callbacks som pipelinesvaret, så
                    # att ett steg som når max_rounds eller ett provider-fel
                    # persisteras på sessionen i stället för att tystna.
                    outcome_callback=self.outcome_callback,
                    checkpoint_callback=self.checkpoint_callback,
                    interrupt_handler=self.interrupt_handler,
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

        return self._finish(ChatResponse(
            text=current_prompt,
            input_tokens=total_in,
            output_tokens=total_out,
            finish_reason="stop",
        ))
