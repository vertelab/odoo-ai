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

Arv av ai.coworker.session: interface_key, text_selection och frontend_info
— de tre värden som powerbox-ytan bär och som kärnan inte har.

Kontexten till LLM:en byggs på ETT ställe: kärnans
`_build_injection_prompt` / `_inject_session_record` (ai_coworker.py).
Den här modulen bygger ingen egen kontext — en parallell beräkning som ser
ut att användas men aldrig når modellen är en dubblett som driver isär
(complete-powerbox-transcript 5.1).
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
    """Powerbox transcript-session — interface point and frontend context."""

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

    @api.model_create_multi
    def create(self, vals_list):
        """Fånga interface_key/text_selection/frontend_info ur env.context
        när sessionen skapas av core:s `powerbox()` (D2).

        Core känner inte till powerbox-ytan — bryggan lägger värdena i
        context (se models/ai_coworker.py) och den här hooken skriver dem
        på sessionen. No-op när nycklarna saknas.
        """
        ctx = self.env.context
        ik = ctx.get('_ai_interface_key')
        ts = ctx.get('_ai_text_selection')
        fi = ctx.get('_ai_frontend_info')
        if ik or ts or fi:
            for vals in vals_list:
                if ik and not vals.get('interface_key'):
                    vals['interface_key'] = ik
                if ts and not vals.get('text_selection'):
                    vals['text_selection'] = ts
                if fi and not vals.get('frontend_info'):
                    vals['frontend_info'] = fi
        return super().create(vals_list)
