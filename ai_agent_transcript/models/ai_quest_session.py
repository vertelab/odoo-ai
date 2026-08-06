# -*- coding: utf-8 -*-
##############################################################################
#
#    Copyright (C) 2024- Vertel AB (<https://vertel.se>).
#
##############################################################################

import json
import logging
import random

from odoo import models, fields, api, _
from odoo.exceptions import AccessError, UserError

_logger = logging.getLogger(__name__)


class AIQuestSession(models.Model):
    """Powerbox transcript session — full context injection for powerbox flows.
    
    Extends the session to support the powerbox pattern:
    1. Stores the interface_key that triggered the session
    2. Stores the composer reference
    3. Builds a comprehensive transcript context including:
       - Record data (from ai_agent_context)
       - Chatter history (from ai_agent_context) 
       - Selected text
       - Frontend info
       - System prompt from the composer
    4. Provides methods to build the full prompt context
    
    This mirrors the Enterprise create_ai_draft_channel() pattern where
    the channel's ai_env_context is pre-populated with all context info.
    """
    _inherit = 'ai.quest.session'

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
        string="Interface Key",
        help="The interface point that triggered this session."
    )
    composer_id = fields.Many2one(
        'ai.composer',
        string="AI Composer",
        help="The composer rule that mapped this interface to a quest."
    )
    text_selection = fields.Text(
        string="Selected Text",
        help="Text selected by the user when triggering the AI "
             "(e.g., for rewrite operations)."
    )
    frontend_info = fields.Text(
        string="Frontend Info",
        help="Additional info provided by the frontend "
             "(e.g., full record data from form view)."
    )
    transcript_context = fields.Text(
        string="Transcript Context",
        compute='_compute_transcript_context',
        help="The full transcript context built for the AI. "
             "This is what gets injected into the system prompt. "
             "Cached until the session is processed."
    )
    powerbox_prompts = fields.Text(
        string="Powerbox Prompts",
        help="Quick prompts from the composer, one per line."
    )

    @api.depends(
        'composer_id', 'context_json', 'context_chatter',
        'text_selection', 'frontend_info'
    )
    def _compute_transcript_context(self):
        """Build the full transcript context for the AI session.
        
        This mirrors the Enterprise pattern where create_ai_draft_channel()
        builds a model_context list and sets it as ai_env_context.
        """
        for session in self:
            context_parts = []

            # 1. Composer's default prompt (if any)
            if session.composer_id and session.composer_id.default_prompt:
                context_parts.append(session.composer_id.default_prompt)

            # 2. Record context (from ai_agent_context)
            record_model = session.context_record_model
            if record_model and session.context_record_id:
                context_parts.append(
                    f"You were called within an Odoo {record_model} record. "
                    f"Your answers should take the record's details into account."
                )
                if session.context_json:
                    context_parts.append(
                        f"The following JSON contains all of the record's details:\n"
                        f"```json\n{session.context_json}\n```"
                    )

            # 3. Frontend info (from form view, if available)
            if session.frontend_info:
                context_parts.append(
                    f"The following JSON contains additional record details "
                    f"from the frontend:\n```json\n{session.frontend_info}\n```"
                )

            # 4. Chatter history
            if session.context_chatter:
                context_parts.append(
                    f"The Odoo record has associated correspondence (chatter). "
                    f"Previous messages, from oldest to newest:\n"
                    f"{session.context_chatter}"
                )

            # 5. Selected text (for rewrite operations)
            if session.text_selection and session.interface_key == "html_field_text_select":
                context_parts.append(
                    f"The text that you will be rewriting is the following:\n"
                    f"{session.text_selection}"
                )

            # 6. Formatting instruction
            context_parts.append(
                "ALWAYS FORMAT YOUR ANSWERS USING MARKDOWN. "
                "Avoid using HTML. Don't use unnecessary formatting "
                "like code blocks if not needed."
            )

            session.transcript_context = "\n\n".join(context_parts)

    def init_powerbox_session(self, interface_key, record=None,
                              text_selection=None, frontend_info=None):
        """Initialize a powerbox session with full transcript context.
        
        This is the main entry point for powerbox flows. It:
        1. Finds the matching composer for this interface/model
        2. Sets up the session with the composer's quest
        3. Injects record context (fields + chatter)
        4. Builds the full transcript context
        
        :param interface_key: The interface point identifier
        :param record: Optional record to use as context
        :param text_selection: Optional selected text
        :param frontend_info: Optional frontend-supplied record data
        :return: self (the session record)
        """
        self.ensure_one()

        # Determine the model for composer matching
        model_name = record._name if record else None

        # Find matching composer (or create default)
        composer = self.env['ai.composer'].find_composer(
            interface_key, model_name
        )

        values = {
            'interface_key': interface_key,
        }

        if composer:
            values['composer_id'] = composer.id
            values['ai_quest_id'] = composer.ai_quest_id.id
            values['powerbox_prompts'] = composer.available_prompts

            # Set quest from composer if not already set
            if not self.ai_quest_id:
                values['ai_quest_id'] = composer.ai_quest_id.id

        # Inject record context
        if record and record.exists():
            if hasattr(self, 'set_context_record'):
                self.set_context_record(record)

        # Store additional context
        if text_selection:
            values['text_selection'] = text_selection
        if frontend_info:
            values['frontend_info'] = frontend_info

        self.write(values)
        return self

    @api.model
    def open_powerbox_quest(self, interface_key, record_model=None,
                            record_id=None, text_selection=None,
                            frontend_info=None):
        """Create a powerbox session and return the channel/chat info.
        
        High-level API called from the frontend (JS powerbox handlers).
        This mirrors the Enterprise create_ai_draft_channel() RPC.
        
        :param interface_key: The interface point identifier
        :param record_model: The model the user was viewing
        :param record_id: The record ID the user was viewing
        :param text_selection: Optional selected text
        :param frontend_info: Optional frontend data JSON
        :return: dict with session_id, quest info, and prompts
        """
        # Get the record if available
        record = None
        if record_model and record_id:
            try:
                record = self.env[record_model].browse(int(record_id))
                if not record.exists():
                    record = None
            except Exception:
                _logger.warning(
                    "Powerbox: could not load record %s/%s",
                    record_model, record_id
                )

        # Find matching composer
        composer = self.env['ai.composer'].find_composer(
            interface_key, record_model
        )

        if not composer:
            raise UserError(
                _("No AI composer configured for '%s'. "
                  "Please contact your administrator.", interface_key)
            )

        quest = composer.ai_quest_id
        if not quest:
            raise UserError(
                _("The composer '%s' has no AI Quest assigned. "
                  "Please contact your administrator.", composer.name)
            )

        # Create session via quest
        session = quest.quest_init(record=record)

        # Initialize powerbox context
        session.init_powerbox_session(
            interface_key=interface_key,
            record=record,
            text_selection=text_selection,
            frontend_info=frontend_info,
        )

        # Get random prompts (max 3)
        prompt_lines = composer.get_available_prompts_list()
        random_prompts = random.sample(
            prompt_lines, min(3, len(prompt_lines))
        ) if prompt_lines else []

        return {
            'session_id': session.id,
            'quest_id': quest.id,
            'quest_name': quest.name,
            'prompts': random_prompts,
            'context_record_model': session.context_record_model,
            'context_record_id': session.context_record_id,
        }

    def get_powerbox_context_for_ai(self):
        """Return the transcript context to be injected into the AI prompt.
        
        This is called by the quest execution flow to get the full
        context that should be included in the system prompt.
        
        :return: String with the full transcript context
        """
        self.ensure_one()
        if self.transcript_context:
            return self.transcript_context
        # Build if not yet computed
        self._compute_transcript_context()
        return self.transcript_context or ""

    def _extra_context(self):
        """Override _extra_context to inject powerbox transcript.
        
        Called by the quest when building the AI system prompt.
        This ensures transcript context is always included.
        """
        context = super()._extra_context() if hasattr(super(), '_extra_context') else ""

        if self.interface_key and self.interface_key.startswith('powerbox'):
            transcript = self.get_powerbox_context_for_ai()
            if transcript:
                context = transcript + "\n\n" + context

        return context
