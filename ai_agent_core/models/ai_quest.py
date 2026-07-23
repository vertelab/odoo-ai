# -*- coding: utf-8 -*-
"""
Wire AgentLoop into ai.quest (T3.3).

Extends ai.quest with use_core_loop flag.
When enabled, quest.run() uses our AgentLoop instead of LangGraph.
"""

import asyncio
import json
import logging

from odoo import models, fields, api

_logger = logging.getLogger(__name__)


class AIQuest(models.Model):
    """Extend ai.quest to support AgentLoop."""
    _inherit = 'ai.quest'

    use_core_loop = fields.Boolean(
        string='Use Core Agent Loop',
        default=False,
        help='Use the new ai_agent_core loop instead of LangGraph.',
    )

    def run_with_core_loop(self, prompt, record=None):
        """Run this quest using the new AgentLoop.

        Called from quest.run() when use_core_loop is True.
        Creates a session, runs the agent loop, and returns the result.
        """
        self.ensure_one()

        # Create or get active session
        session = self.env['ai.quest.session'].quest_init(self)

        # Build system prompt from quest description + context
        system_prompt = self.description or ""
        if self.use_time_context:
            from datetime import datetime
            system_prompt += f"\n\nCurrent time: {datetime.now().isoformat()}"

        # Resolve model and provider
        model = "cerebras/gpt-oss-120b"  # default
        if self.ai_agent_ids:
            for agent_line in self.ai_agent_ids:
                llm = agent_line.ai_agent_id.ai_agent_llm_id
                if llm and llm.model_name:
                    model = llm.model_name
                    break

        # Build tools from quest's agents
        tools_data = []
        for agent_line in self.ai_agent_ids:
            agent = agent_line.ai_agent_id
            for tool_line in agent.ai_tool_ids:
                if tool_line.ai_tool_id:
                    tools_data.append({
                        'name': tool_line.ai_tool_id.name or 'tool',
                        'description': tool_line.ai_tool_id.description or '',
                        'parameters': tool_line.ai_tool_id.parameters or {},
                    })

        # Run the agent loop
        try:
            result = _run_async_loop(
                model=model,
                system_prompt=system_prompt,
                prompt=prompt,
                tools_data=tools_data,
            )
        except Exception as e:
            _logger.error("AgentLoop failed: %s", e, exc_info=True)
            result = f"Error: {e}"

        # Store in session
        session.config_json = json.dumps({
            'model': model,
            'system_prompt': system_prompt,
        })
        session.status = 'done'
        session.enddate = fields.Datetime.now()

        return result

    def run(self, **kwargs):
        """Override run() to use AgentLoop when enabled."""
        if self.use_core_loop:
            prompt = kwargs.get('prompt', '')
            if not prompt and kwargs.get('message_body'):
                prompt = kwargs['message_body']
            if not prompt and kwargs.get('message_invoke'):
                msg = kwargs['message_invoke']
                if hasattr(msg, 'content'):
                    prompt = msg.content
                elif isinstance(msg, dict) and 'messages' in msg:
                    for m in msg['messages']:
                        if hasattr(m, 'content'):
                            prompt = m.content
                            break

            if prompt:
                result_text = self.run_with_core_loop(prompt)
                # Format result to match existing run() return format
                from langchain_core.messages import AIMessage
                return {
                    'result': {
                        'messages': [AIMessage(content=result_text)],
                    }
                }

        # Fall back to original LangGraph run
        return super().run(**kwargs)


# ---------------------------------------------------------------------------
# Async helper — runs async loop in sync Odoo context
# ---------------------------------------------------------------------------

def _run_async_loop(model, system_prompt, prompt, tools_data):
    """Run the AgentLoop synchronously from Odoo's sync context."""

    async def _run():
        from odoo.addons.ai_agent_core.core.provider import BifrostProvider
        from odoo.addons.ai_agent_core.core.tools import ToolRegistry, Tool
        from odoo.addons.ai_agent_core.core.loop import AgentLoop, AgentConfig

        provider = BifrostProvider(
            base_url="http://192.168.11.150:8080/v1",
            virtual_key="opencode",
        )

        tools = ToolRegistry()

        # Add Odoo model tools
        for td in tools_data:
            tool_name = td['name']
            async def _model_tool_handler(_name=tool_name, **kwargs):
                return f"Tool '{_name}' called with: {json.dumps(kwargs)}"

            tools.register(Tool(
                name=tool_name,
                description=td.get('description', ''),
                parameters=td.get('parameters', {"type": "object", "properties": {}}),
                handler=_model_tool_handler,
                risk_level="read_only",
                source="odoo_model",
            ))

        loop = AgentLoop(
            provider=provider,
            tools=tools,
            config=AgentConfig(
                model=model,
                system_prompt=system_prompt,
                max_rounds=10,
            ),
        )

        response = await loop.run(prompt)
        return response.text

    result_loop = asyncio.new_event_loop()
    try:
        return result_loop.run_until_complete(_run())
    finally:
        result_loop.close()
