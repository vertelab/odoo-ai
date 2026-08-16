# -*- coding: utf-8 -*-
##############################################################################
#
#    Copyright (C) 2026 Vertel Sverige AB (<https://vertel.se>).
#    All Rights Reserved
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU Affero General Public License as published
#    by the Free Software Foundation, either version 3 of the License, or
#    (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU Affero General Public License for more details.
#
#    You should have received a copy of the GNU Affero General Public License
#    along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
##############################################################################

"""Powerbox transcript-session (ai_agent_transcript → ai_agent_core).

Arv av ai.coworker.session: interface_key + transcript_context.
Bygger den samlade kontexten för powerbox-flöden: rekordfält-JSON,
chatter-historik, frontend-kontext och vald text — med core-hooks
(_ai_serialize_fields_data / _ai_serialize_messages_data).
"""

import logging

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

INTERFACE_KEYS = [
    ("html_field_record", "Write in an HTML field"),
    ("mail_composer", "Write an email"),
    ("html_field_text_select", "Rewrite content"),
    ("chatter_ai_button", "Get help on a record"),
    ("systray_ai_button", "Ask AI for help"),
    ("voice_transcription_component", "Summary Buttons for Voice Transcription"),
    ("powerbox_chat", "Powerbox Chat Quest"),
    ("powerbox_channel", "Powerbox Channel Quest"),
]


class AICoworkerSessionTranscript(models.Model):
    """Powerbox transcript-session — full context injection."""

    _inherit = 'ai.coworker.session'

    interface_key = fields.Selection(
        selection=INTERFACE_KEYS,
        string="Interface Point",
        help="Which interface point triggered this session."
    )
    text_selection = fields.Text(
        string="Selected Text",
        help="Text the user selected (for rewrite operations)."
    )
    frontend_info = fields.Text(
        string="Frontend Info",
        help="Frontend context (active model, view type, etc.) as JSON."
    )
    transcript_context = fields.Text(
        string="Transcript Context",
        compute='_compute_transcript_context',
        help="The full transcript context built for the AI: record fields, "
             "chatter history, frontend info and selected text."
    )

    @api.depends('interface_key', 'text_selection', 'frontend_info')
    def _compute_transcript_context(self):
        """Build the full transcript context for the AI session."""
        for session in self:
            parts = []

            # 1. Frontend info (active model, view type)
            if session.frontend_info:
                parts.append("## Frontend context\n%s" % session.frontend_info)

            # 2. Record fields from context (if any)
            record = session._get_ai_context_record()
            if record and record.exists():
                try:
                    json_data = record._ai_serialize_fields_data(
                        max_fields=session.coworker_id.context_max_fields
                        if session.coworker_id else 100)
                    parts.append(
                        "## Current Record: %s (ID: %s)\n```json\n%s\n```"
                        % (record._name, record.id, json_data))
                except Exception as e:
                    _logger.warning('record serialize failed: %s', e)

            # 3. Chatter history (if record has mail.thread)
            if record and hasattr(record, '_ai_serialize_messages_data'):
                try:
                    chatter = record._ai_serialize_messages_data()
                    if chatter:
                        lines = chatter.split('\n')
                        if len(lines) > 50:
                            lines = lines[-50:]
                            chatter = '\n'.join(lines) + \
                                "\n(older messages omitted)"
                        parts.append("## Chatter history\n%s" % chatter)
                except Exception as e:
                    _logger.warning('chatter serialize failed: %s', e)

            # 4. Selected text (for rewrite operations)
            if session.text_selection:
                parts.append(
                    "## Selected text\n%s" % session.text_selection)

            session.transcript_context = "\n\n".join(parts)
