# -*- coding: utf-8 -*-
"""
Wire DiscussInterruptHandler into discuss.channel — only if ai_agent installed.
"""

import asyncio
import json
import logging

from odoo import models, fields, api

_logger = logging.getLogger(__name__)


class DiscussChannel(models.Model):
    _inherit = 'discuss.channel'

    @api.returns('mail.message', lambda value: value.id)
    def message_post(self, **kwargs):
        message = super().message_post(**kwargs)
        user, ai_quest = self.get_user_and_quest()
        if ai_quest and getattr(ai_quest, 'use_core_loop', False):
            self._send_ai_message_core(message)
        else:
            self.send_ai_message(message)
        return message

    def _send_ai_message_core(self, message):
        user, ai_quest = self.get_user_and_quest()
        if not ai_quest or not ai_quest.use_core_loop:
            return False
        if message.author_id == user.partner_id:
            return False
        if not self._continue_with_chat(ai_quest, message):
            return False
        from odoo.tools.mail import html2plaintext
        prompt = html2plaintext(message.body)
        if not prompt.strip():
            return False
        _logger.info("AgentLoop chat: quest=%s", ai_quest.name)
        try:
            result_text = _run_agent_chat(ai_quest, prompt)
        except Exception as e:
            _logger.error("AgentLoop chat failed: %s", e, exc_info=True)
            result_text = f"Error: {e}"
        if result_text:
            import markdown, re
            from markupsafe import Markup
            answer = markdown.markdown(result_text)
            if not ai_quest.debug:
                answer = re.sub(r'<think>.*?</think>', '', answer, flags=re.DOTALL)
            self.with_user(user).message_post(
                body=Markup(answer), message_type='comment',
                subtype_xmlid='mail.mt_comment')
            return True
        return False


def _run_agent_chat(ai_quest, prompt):
    async def _run():
        from odoo.addons.ai_agent_core.core.provider import BifrostProvider
        from odoo.addons.ai_agent_core.core.tools import ToolRegistry, builtin_tools
        from odoo.addons.ai_agent_core.core.loop import AgentLoop, AgentConfig
        model = "cerebras/gpt-oss-120b"
        provider = BifrostProvider(base_url="http://192.168.11.150:8080/v1", virtual_key="opencode")
        tools = ToolRegistry()
        tools.register_many(builtin_tools())
        loop = AgentLoop(provider=provider, tools=tools, config=AgentConfig(
            model=model, system_prompt=ai_quest.description or "", max_rounds=10))
        resp = await loop.run(prompt)
        return resp.text
    result_loop = asyncio.new_event_loop()
    try:
        return result_loop.run_until_complete(_run())
    finally:
        result_loop.close()
