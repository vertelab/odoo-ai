# -*- coding: utf-8 -*-
"""
Wire AgentLoop into ai.quest (T3.3) — only if ai_agent is installed.

These extensions require ai_agent module. If ai_agent is not installed,
this file is skipped (registered via conditional in __init__.py).
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
        self.ensure_one()
        session = self.env['ai.quest.session'].quest_init(self)
        system_prompt = self.description or ""
        if self.use_time_context:
            from datetime import datetime
            system_prompt += f"\n\nCurrent time: {datetime.now().isoformat()}"
        model = "cerebras/gpt-oss-120b"
        if self.ai_agent_ids:
            for agent_line in self.ai_agent_ids:
                llm = agent_line.ai_agent_id.ai_agent_llm_id
                if llm and llm.model_name:
                    model = llm.model_name
                    break
        try:
            result = _run_async_loop(model=model, system_prompt=system_prompt,
                                      prompt=prompt, tools_data=[])
        except Exception as e:
            _logger.error("AgentLoop failed: %s", e, exc_info=True)
            result = f"Error: {e}"
        session.config_json = json.dumps({'model': model})
        session.status = 'done'
        session.enddate = fields.Datetime.now()
        return result

    def run(self, **kwargs):
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
                from langchain_core.messages import AIMessage
                return {'result': {'messages': [AIMessage(content=result_text)]}}
        return super().run(**kwargs)


def _run_async_loop(model, system_prompt, prompt, tools_data):
    async def _run():
        from odoo.addons.ai_agent_core.core.provider import BifrostProvider
        from odoo.addons.ai_agent_core.core.tools import ToolRegistry, builtin_tools
        from odoo.addons.ai_agent_core.core.loop import AgentLoop, AgentConfig
        provider = BifrostProvider(base_url="http://192.168.11.150:8080/v1", virtual_key="opencode")
        tools = ToolRegistry()
        tools.register_many(builtin_tools())
        loop = AgentLoop(provider=provider, tools=tools, config=AgentConfig(
            model=model, system_prompt=system_prompt, max_rounds=10))
        response = await loop.run(prompt)
        return response.text
    result_loop = asyncio.new_event_loop()
    try:
        return result_loop.run_until_complete(_run())
    finally:
        result_loop.close()
