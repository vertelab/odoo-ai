# -*- coding: utf-8 -*-
"""
Bridge controller — intercept Odoo's /html_editor/generate_text
and route through ai.coworker instead of Odoo's OLG IAP service.

Inherits HTML_Editor controller from html_editor module (CE).
Overrides generate_text() to optionally use ai.coworker for generation.
"""

import logging

from odoo import http
from odoo.http import request

from odoo.addons.html_editor.controllers.main import HTML_Editor

_logger = logging.getLogger(__name__)


class AIQuestHtmlEditorBridge(HTML_Editor):
    """Override generate_text to route through ai.coworker when configured."""

    @http.route(["/web_editor/generate_text", "/html_editor/generate_text"],
                type="json", auth="user")
    def generate_text(self, prompt, conversation_history):
        """Override: route through ai.coworker if bridge is configured.

        Falls back to original OLG IAP call if no quest is configured
        or the quest fails.
        """
        # Check if bridge is enabled
        IrConfig = request.env['ir.config_parameter'].sudo()
        quest_id = IrConfig.get_param('ai_coworker_bridge.html_editor_quest_id')

        if quest_id:
            try:
                quest = request.env['ai.coworker'].browse(int(quest_id))
                if quest.exists():
                    # Build full prompt from conversation_history
                    full_prompt = self._build_quest_prompt(
                        prompt, conversation_history)
                    # Get system prompt from quest
                    system_prompt = quest.description or ''
                    if quest.identity_id:
                        system_prompt = (
                            quest.identity_id.system_prompt or system_prompt)
                    # Run quest
                    result = quest.run(
                        prompt=full_prompt, system_prompt=system_prompt)
                    if result and not result.startswith('Error:'):
                        _logger.info(
                            'html_editor bridge: quest %s generated '
                            'response (%d chars)', quest.name, len(result))
                        return {
                            'status': 'success',
                            'content': result,
                        }
                    else:
                        _logger.warning(
                            'html_editor bridge: quest %s failed: %s',
                            quest.name, result)
            except Exception as e:
                _logger.error(
                    'html_editor bridge error: %s', e, exc_info=True)

        # Fallback to original Odoo OLG behavior
        return super().generate_text(prompt, conversation_history)

    def _build_quest_prompt(self, prompt, conversation_history):
        """Convert Odoo's conversation_history format to a quest prompt.

        conversation_history format:
            [{role: 'system'|'user'|'assistant', content: '...'}, ...]

        Returns a single string prompt suitable for ai.coworker.run().
        """
        parts = []

        # Extract system messages
        system_msgs = [
            m for m in (conversation_history or [])
            if m.get('role') == 'system'
        ]
        for m in system_msgs:
            content = m.get('content', '').strip()
            if content and content != (
                'You are a helpful assistant, your goal '
                'is to help the user write their document.'
            ):
                parts.append(f"## System instructions\n{content}")

        # Extract conversation history (user + assistant)
        chat_messages = [
            m for m in (conversation_history or [])
            if m.get('role') in ('user', 'assistant')
            and m.get('content', '').strip()
        ]
        if chat_messages:
            # Skip the initial assistant greeting if present
            if (chat_messages[0].get('role') == 'assistant'
                    and chat_messages[0].get('content', '').strip()
                    == 'What do you need ?'):
                chat_messages = chat_messages[1:]

        if chat_messages:
            history_lines = []
            for m in chat_messages[-20:]:  # Last 20 messages max
                role = m.get('role', 'user')
                content = m.get('content', '').strip()
                history_lines.append(f"[{role}]: {content}")
            if history_lines:
                parts.append(
                    "## Conversation history\n" + "\n".join(history_lines)
                )

        # The current user prompt
        parts.append(f"## Current request\n{prompt}")

        return "\n\n".join(parts)
