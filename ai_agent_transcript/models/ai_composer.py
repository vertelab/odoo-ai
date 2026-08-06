# -*- coding: utf-8 -*-
##############################################################################
#
#    Copyright (C) 2024- Vertel AB (<https://vertel.se>).
#
#    Ported and adapted from Odoo Enterprise ai/models/ai_composer.py
#    Original copyright: Odoo S.A., OEEL-1 License
#
##############################################################################

import logging

from odoo import fields, models, _, api
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# Interface keys matching Odoo Enterprise ai_composer.py
# Each key represents a specific point in the UI where AI can be invoked
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


class AIComposer(models.Model):
    """AI Composer — maps interface points to AI Quests with default prompts.

    Ported from Odoo Enterprise ai/models/ai_composer.py.
    
    A Composer defines:
    1. Which interface point triggers it (interface_key)
    2. Which models it applies to (focused_models)
    3. Which AI Quest handles the request (ai_quest_id)
    4. The default system prompt (default_prompt)
    5. Quick-action prompts available to the user (available_prompts)
    
    This is the powerbox: when a user invokes AI from a specific place
    (e.g., an HTML field), the system finds the matching composer and
    creates a session with the appropriate Quest and context.
    """
    _name = "ai.composer"
    _description = "AI Composer — Powerbox interface to Quest mapping"
    _order = "interface_key, name"

    def _get_default_quest(self):
        """Return the default AI Quest for new composers."""
        default = self.env.ref(
            'ai_agent_transcript.ai_quest_powerbox_default',
            raise_if_not_found=False
        )
        return default.id if default else False

    name = fields.Char(
        "Rule Name",
        required=True,
        help="Identifier for this powerbox rule. Shown in the configuration UI."
    )
    interface_key = fields.Selection(
        selection=INTERFACE_KEYS,
        string="Interface Point",
        required=True,
        help="Where in the Odoo UI this composer is triggered from."
    )
    focused_models = fields.Many2many(
        'ir.model',
        string="Models",
        help="Limit this composer to specific models. "
             "Leave empty to make it available for all models."
    )
    ai_quest_id = fields.Many2one(
        'ai.quest',
        string="AI Quest",
        default=_get_default_quest,
        required=True,
        help="The AI Quest that handles this powerbox interaction."
    )
    default_prompt = fields.Text(
        "Default Instructions",
        help="Default system prompt/instructions for the AI. "
             "This is injected as the first context message when "
             "the quest session is created."
    )
    is_system_default = fields.Boolean(
        "System Default",
        default=False,
        readonly=True,
        copy=False,
        help="System default composers cannot be deleted."
    )
    active = fields.Boolean(
        default=True,
        help="Inactive composers are ignored."
    )
    available_prompts = fields.Text(
        "Quick Prompts",
        help="One prompt per line. These appear as quick-action buttons "
             "for the user when the AI is invoked."
    )

    @api.ondelete(at_uninstall=False)
    def _unlink_except_default_rules(self):
        """Prevent deletion of system default composers."""
        if any(composer.is_system_default for composer in self):
            raise UserError(
                _('System default powerbox rules cannot be removed.')
            )

    def copy_data(self, default=None):
        """Add '(copy)' suffix when duplicating composers."""
        default = dict(default or {})
        vals_list = super().copy_data(default=default)
        if 'name' not in default:
            for composer, vals in zip(self, vals_list):
                vals['name'] = _("%s (copy)", composer.name)
        return vals_list

    def find_composer(self, interface_key, model_name=None):
        """Find the best matching composer for an interface and model.
        
        Priority:
        1. Composer matching both interface_key and the specific model
        2. Composer matching interface_key with no model restriction
        3. None (no match)
        
        :param interface_key: The interface point identifier
        :param model_name: Optional model technical name (e.g., 'res.partner')
        :return: ai.composer record or None
        """
        domain = [
            ('interface_key', '=', interface_key),
            ('active', '=', True),
        ]
        
        # Try model-specific first
        if model_name:
            model = self.env['ir.model'].search(
                [('model', '=', model_name)], limit=1
            )
            if model:
                composers = self.search(
                    domain + [('focused_models', 'in', model.id)],
                    limit=1, order="create_date DESC"
                )
                if composers:
                    return composers
        
        # Fall back to model-agnostic composer
        return self.search(
            domain + [('focused_models', '=', False)],
            limit=1, order="create_date DESC"
        )

    def get_available_prompts_list(self):
        """Return available prompts as a list of strings."""
        self.ensure_one()
        if not self.available_prompts:
            return []
        return [
            line.strip()
            for line in self.available_prompts.splitlines()
            if line.strip()
        ]
