# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.
#
# Ported and adapted from Odoo Enterprise ai/models/mail_thread.py
# Original copyright: Odoo S.A., OEEL-1 License

import logging

from odoo import models

_logger = logging.getLogger(__name__)


class MailThread(models.AbstractModel):
    """Add chatter history serialization to mail.thread models.
    
    Ported from Odoo Enterprise ai/models/mail_thread.py.
    When an AI Quest is triggered from a record with a chatter,
    the full message history is serialized and injected into context.
    """
    _inherit = 'mail.thread'

    def _ai_serialize_messages_data(self):
        """Serialize the chatter message history for AI context.
        
        Returns messages from oldest to newest in a readable format:
        (Subtype) Author: Body
        
        Ported from Odoo Enterprise: mail_thread.py::_ai_serialize_messages_data()
        
        :return: String of serialized chatter messages, oldest first
        :rtype: str
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
                # Strip HTML tags for cleaner context
                if hasattr(body, 'striptags'):
                    body = body.striptags()
                body = body.strip()
            
            chatter_messages.append(
                f"({subtype_name}) {author_name}: {body}"
            )

        # Messages are stored newest-first by default; reverse for chronological
        chatter_messages = list(reversed(chatter_messages))
        return "\n".join(chatter_messages)

    def _ai_initialise_context(
        self, caller_component, text_selection=None, front_end_info=None
    ):
        """Build AI context including chatter history.
        
        Extends the base _ai_build_record_context with chatter serialization.
        This mirrors the Enterprise pattern where mail.thread adds chatter
        context on top of base model context.
        
        :param caller_component: Calling component identifier
        :param text_selection: Optional selected text
        :param front_end_info: Optional frontend-supplied record data
        :return: List of context strings
        :rtype: list
        """
        context = super()._ai_build_record_context(
            caller_component, text_selection
        )

        # Add chatter history (except for text-rewrite contexts)
        if caller_component != "html_field_text_select":
            chatter = self._ai_serialize_messages_data()
            if chatter:
                # Insert before the formatting instruction (last element)
                chatter_context = (
                    f"The Odoo record, from which you were called, can also "
                    f"have associated correspondence tied to it. All those "
                    f"messages and notes are included in the chatter, a "
                    f"chat-like area in the record's form view. The previous "
                    f"chatter correspondence, from oldest to newest, for this "
                    f"record is this:\n{chatter}"
                )
                context.insert(-1, chatter_context)

        return context
