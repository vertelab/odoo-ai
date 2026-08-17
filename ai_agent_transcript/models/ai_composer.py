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
    """AI Composer — maps interface points to AI coworkers with prompts.

    Ported from ai_agent_transcript (legacy ai.quest) to ai_agent_core
    (ai.coworker). A composer defines:
    1. Which interface point triggers it (interface_key)
    2. Which models it applies to (focused_models)
    3. Which AI Medarbetare handles the request (coworker_id)
    4. The default system prompt (default_prompt)
    5. Quick-action prompts available to the user (available_prompts)
    """
    _name = "ai.composer"
    _description = "AI Composer — Powerbox interface to coworker mapping"
    _order = "interface_key, name"

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
    coworker_id = fields.Many2one(
        'ai.coworker',
        string="AI Medarbetare",
        help="Coworker that handles the request at this interface point."
    )
    default_prompt = fields.Text(
        string="Default Prompt",
        help="System prompt for the coworker when triggered here."
    )
    available_prompts = fields.Text(
        string="Quick-Action Prompts",
        help="One prompt per line. Shown to the user as quick actions."
    )
    is_system_default = fields.Boolean(
        string="System Default",
        default=False,
        help="System defaults cannot be deleted."
    )
    active = fields.Boolean(default=True)

    @api.constrains('coworker_id', 'interface_key', 'active')
    def _check_system_defaults(self):
        """Skydda system-default composers mot oavsiktlig borttagning."""
        for rec in self:
            if rec.is_system_default and not rec.active:
                raise UserError(_(
                    "System default composers cannot be deactivated. "
                    "Disable 'System Default' first."
                ))

    def _unlink_except_default_rules(self):
        """Block unlink of system-default composers (unless superuser)."""
        defaults = self.filtered('is_system_default')
        if defaults and not self.env.su:
            raise UserError(_(
                "System default composers cannot be deleted. "
                "Disable 'System Default' first."
            ))
        return True

    def unlink(self):
        self._unlink_except_default_rules()
        return super().unlink()

    def copy_data(self, default=None):
        """When copying, clear the system-default flag."""
        data = super().copy_data(default=default)
        for d in data:
            d['is_system_default'] = False
        return data

    def find_composer(self, interface_key, model_name=None):
        """Find the best matching composer for an interface point.

        Priority:
        1. Composer with focused_models containing the model (if given)
        2. Composer with empty focused_models (applies to all)
        3. None

        Args:
            interface_key: The interface point identifier
            model_name: Optional model technical name (e.g., 'project.task')

        Returns:
            ai.composer recordset (empty if no match)
        """
        domain = [
            ('interface_key', '=', interface_key),
            ('active', '=', True),
        ]
        candidates = self.search(domain)
        if not candidates:
            return self.browse()

        if model_name:
            model = self.env['ir.model'].search(
                [('model', '=', model_name)], limit=1)
            if model:
                specific = candidates.filtered(
                    lambda c: model in c.focused_models)
                if specific:
                    return specific[:1]

        generic = candidates.filtered(lambda c: not c.focused_models)
        if generic:
            return generic[:1]

        return candidates[:1]

    def get_available_prompts_list(self):
        """Parse available_prompts (newline-separated) into a list."""
        self.ensure_one()
        if not self.available_prompts:
            return []
        return [p.strip() for p in self.available_prompts.splitlines() if p.strip()]
