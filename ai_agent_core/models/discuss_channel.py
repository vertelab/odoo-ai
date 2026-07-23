# -*- coding: utf-8 -*-
"""
Wire DiscussInterruptHandler into Odoo discuss.channel (HITL-002).

When a quest has use_core_loop=True, the agent loop uses
DiscussInterruptHandler instead of AutoInterruptHandler.
"""

import asyncio
import json
import logging
import time

from odoo import models, fields, api

_logger = logging.getLogger(__name__)


class DiscussChannel(models.Model):
    """Extend discuss.channel with AgentLoop + interrupt support."""
    _inherit = 'discuss.channel'

    @api.returns('mail.message', lambda value: value.id)
    def message_post(self, **kwargs):
        """Override: use AgentLoop when quest has use_core_loop=True."""
        message = super().message_post(**kwargs)
        
        # Check if this channel has a core-loop quest
        user, ai_quest = self.get_user_and_quest()
        if ai_quest and ai_quest.use_core_loop:
            self.send_ai_message_core(message)
        else:
            self.send_ai_message(message)
        
        return message

    def send_ai_message_core(self, message):
        """Send message using the new AgentLoop with DiscussInterruptHandler.

        Called instead of the original send_ai_message when
        the quest has use_core_loop=True.
        """
        user, ai_quest = self.get_user_and_quest()

        if not ai_quest or not ai_quest.use_core_loop:
            return False

        if message.author_id == user.partner_id:
            return False

        # Check trigger words
        if not self._continue_with_chat(ai_quest, message):
            return False

        # Get prompt from message
        from odoo.tools.mail import html2plaintext
        prompt = html2plaintext(message.body)
        if not prompt.strip():
            return False

        _logger.info("AgentLoop chat: quest=%s prompt=%s...", ai_quest.name, prompt[:50])

        # Run the agent loop with DiscussInterruptHandler
        try:
            # Build handler
            from odoo.addons.ai_agent_core.core.interrupt import DiscussInterruptHandler

            handler = DiscussInterruptHandler(
                channel=self,
                bot_user=user,
                env=self.env,
            )

            # Run async loop
            loop = asyncio.new_event_loop()
            try:
                result_text = loop.run_until_complete(
                    _run_agent_with_interrupt(
                        ai_quest=ai_quest,
                        prompt=prompt,
                        handler=handler,
                    )
                )
            finally:
                loop.close()

        except Exception as e:
            _logger.error("AgentLoop chat failed: %s", e, exc_info=True)
            result_text = f"❌ Error: {e}"

        # Post response
        if result_text:
            import markdown
            import re
            from markupsafe import Markup

            if ai_quest.debug:
                answer = markdown.markdown(result_text)
            else:
                answer = re.sub(
                    r'<think>.*?</think>', '',
                    markdown.markdown(result_text),
                    flags=re.DOTALL,
                )

            self.with_user(user).message_post(
                body=Markup(answer),
                message_type='comment',
                subtype_xmlid='mail.mt_comment',
            )
            return True

        return False


async def _run_agent_with_interrupt(ai_quest, prompt, handler):
    """Run the AgentLoop with an interrupt handler."""
    from odoo.addons.ai_agent_core.core.provider import BifrostProvider
    from odoo.addons.ai_agent_core.core.tools import ToolRegistry, Tool, builtin_tools
    from odoo.addons.ai_agent_core.core.loop import AgentLoop, AgentConfig

    # Resolve model
    model = "cerebras/gpt-oss-120b"
    system_prompt = ai_quest.description or ""
    if ai_quest.use_time_context:
        from datetime import datetime
        system_prompt += f"\n\nCurrent time: {datetime.now().isoformat()}"

    if ai_quest.ai_agent_ids:
        for agent_line in ai_quest.ai_agent_ids:
            llm = agent_line.ai_agent_id.ai_agent_llm_id
            if llm and llm.model_name:
                model = llm.model_name
                break

    # Provider
    provider = BifrostProvider(
        base_url="http://192.168.11.150:8080/v1",
        virtual_key="opencode",
    )

    # Tools
    tools = ToolRegistry()
    tools.register_many(builtin_tools())

    # Add Odoo model tools from agent configuration
    if ai_quest.ai_agent_ids:
        for agent_line in ai_quest.ai_agent_ids:
            for tool_line in agent_line.ai_agent_id.ai_tool_ids:
                if tool_line.ai_tool_id:
                    tool_name = tool_line.ai_tool_id.name
                    tools.register(Tool(
                        name=tool_name,
                        description=tool_line.ai_tool_id.description or '',
                        parameters=tool_line.ai_tool_id.parameters or {},
                        handler=_make_odoo_tool_handler(tool_name),
                        risk_level="read_only",
                        source="odoo_model",
                    ))

    # Steel: check for mid-turn messages before LLM call
    steers = await handler.drain_steer()
    steer_prompt = ""
    if steers:
        steer_prompt = "\n\n[Mid-turn messages from user]\n" + "\n".join(steers)

    # Run loop
    loop_obj = AgentLoop(
        provider=provider,
        tools=tools,
        config=AgentConfig(
            model=model,
            system_prompt=system_prompt,
            max_rounds=10,
        ),
    )

    response = await loop_obj.run(prompt + steer_prompt)
    return response.text


def _make_odoo_tool_handler(tool_name):
    """Create an async handler for an Odoo model tool."""
    async def handler(**kwargs):
        return f"Tool '{tool_name}' called with: {json.dumps(kwargs)}"
    return handler
