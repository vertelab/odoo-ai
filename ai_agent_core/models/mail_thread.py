# -*- coding: utf-8 -*-
"""Mail thread extensions — chatter history for AI context.

Ported from ai_agent_context/models/mail_thread.py.
Provides _ai_serialize_messages_data() on mail.thread models.
"""

import logging

from odoo import models

_logger = logging.getLogger(__name__)


class MailThread(models.AbstractModel):
    """Add chatter history serialization to mail.thread models."""

    _inherit = 'mail.thread'

    def _ai_serialize_messages_data(self):
        """Serialize the chatter message history for AI context.

        Returns messages from oldest to newest in a readable format:
        (Subtype) Author: Body

        :return: String of serialized chatter messages, oldest first
        """
        chatter_messages = []
        for message in self.message_ids:
            subtype_name = (
                message.subtype_id.name if message.subtype_id else "Message"
            )
            author_name = (
                message.author_id.name if message.author_id else "System"
            )
            body = ""
            if message.body:
                body = message.body
                if hasattr(body, 'striptags'):
                    body = body.striptags()
                body = body.strip()

            chatter_messages.append(
                f"({subtype_name}) {author_name}: {body}"
            )

        chatter_messages = list(reversed(chatter_messages))
        return "\n".join(chatter_messages)
