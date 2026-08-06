# -*- coding: utf-8 -*-
##############################################################################
#
#    Copyright (C) 2024- Vertel AB (<https://vertel.se>).
#
##############################################################################

import logging

from odoo import models, fields

_logger = logging.getLogger(__name__)


class AIQuest(models.Model):
    """Add powerbox init type to AI Quests."""
    _inherit = 'ai.quest'

    init_type = fields.Selection(
        selection_add=[('powerbox', 'Powerbox')],
        ondelete={'powerbox': 'cascade'},
    )

    interface_key = fields.Selection(
        selection=[
            ("html_field_record", "Write in an HTML field"),
            ("mail_composer", "Write an email"),
            ("html_field_text_select", "Rewrite content"),
            ("chatter_ai_button", "Get help on a record"),
            ("systray_ai_button", "Ask AI for help"),
            ("voice_transcription_component", "Summary Buttons for Voice Transcription"),
            ("powerbox_chat", "Powerbox Chat Quest"),
            ("powerbox_channel", "Powerbox Channel Quest"),
        ],
        string="Default Interface Key",
        help="Interface key this quest is designed for. "
             "Used as fallback when no composer matches."
    )
