# -*- coding: utf-8 -*-
"""user_mail_ai.rule — användarens regler i klartext.

Utvärdering: deterministisk pre-filter (sender/subject/category) + LLM
för condition_kind='llm' (regeltexten injiceras i klassificeringsprompten).
Standing rules från ai.coworker.hitl-trust-ladder importeras med
source='trust_ladder'.
"""

import json
import logging

from odoo import models, fields

_logger = logging.getLogger(__name__)


class UserMailAiRule(models.Model):
    _name = 'user_mail_ai.rule'
    _description = 'Mail-hjälpredan: användarregel'
    _order = 'priority, id'

    user_id = fields.Many2one(
        'res.users', string='Användare', required=True, ondelete='cascade',
        index=True, default=lambda self: self.env.user)
    name = fields.Char('Regel', required=True,
                       help='Klartext, t.ex. "arkivera nyhetsbrev jag inte öppnat"')
    priority = fields.Integer('Prioritet', default=10,
                              help='Lägre tal = högre prioritet.')
    active = fields.Boolean('Aktiv', default=True)
    condition_kind = fields.Selection([
        ('sender', 'Avsändare'),
        ('subject', 'Ämne'),
        ('category', 'Kategori'),
        ('llm', 'LLM (fri text)'),
    ], string='Villkorstyp', required=True, default='sender')
    condition_text = fields.Text('Villkor', required=True)
    action = fields.Selection([
        ('move_to_folder', 'Flytta till mapp'),
        ('flag', 'Flagga som viktig'),
        ('nudge', 'Nudgea mig'),
        ('ignore', 'Ignorera'),
        ('create_event', 'Skapa kalenderhändelse'),
        ('draft_reply', 'Dra svarsutkast'),
        ('block', 'Blockera'),
        ('send_to_specialist', 'Skicka till specialist'),
    ], string='Åtgärd', required=True)
    action_config = fields.Text(
        'Åtgärdskonfig (JSON)', default='{}',
        help='T.ex. {"folder": "AI/Arkiv"} för move_to_folder.')
    source = fields.Selection([
        ('user', 'Användare'),
        ('seed', 'Default'),
        ('trust_ladder', 'Trust-ladder'),
    ], string='Källa', default='user', readonly=True)

    _sql_constraints = [
        ('unique_user_name', 'unique(user_id, name)',
         'Regelnamnet måste vara unikt per användare!'),
    ]

    def _parse_config(self):
        try:
            cfg = json.loads(self.action_config or '{}')
            return cfg if isinstance(cfg, dict) else {}
        except Exception:
            return {}

    def _matches(self, mail):
        """Deterministisk matchning (sender/subject/category)."""
        ct = (self.condition_text or '').strip()
        if not ct:
            return False
        if self.condition_kind == 'sender':
            return ct.lower() in (mail.from_email or '').lower()
        if self.condition_kind == 'subject':
            return ct.lower() in (mail.subject or '').lower()
        if self.condition_kind == 'category':
            return ct == mail.category
        # llm matchas via klassificeringsutdata (matched_rules)
        return False
