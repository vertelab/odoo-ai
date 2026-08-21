# -*- coding: utf-8 -*-
"""Per-användare-koppling: vilken hjälpreda tjänar användaren."""

import logging

from odoo import models, fields

_logger = logging.getLogger(__name__)


class ResUsers(models.Model):
    _inherit = 'res.users'

    ai_coworker_id = fields.Many2one(
        'ai.coworker', string='AI Medarbetare (mail)',
        help='Hjälpredan som tjänar denna användare. Tomt → default '
             'Mail-hjälpredan används.')
    # ── Skiva 3: intresseprofil + digest ──
    ai_profile_text = fields.Text('Intresseprofil (genererad)')
    ai_profile_embedding = fields.Text('Profil-embedding (JSON)')
    ai_profile_updated = fields.Datetime('Profil uppdaterad')
    ai_digest_enabled = fields.Boolean('Daglig digest', default=False)
    ai_digest_weekday = fields.Selection([
        ('0', 'Måndag'), ('1', 'Tisdag'), ('2', 'Onsdag'),
        ('3', 'Torsdag'), ('4', 'Fredag'), ('5', 'Lördag'),
        ('6', 'Söndag'),
    ], string='Veckodigest dag', default='4')

    ai_newsletter_move_enabled = fields.Boolean(
        'Flytta nyhetsbrev automatiskt', default=False,
        help='Sätts via HITL-godkännande (Skiva 2). Ersätts av regelmodellen '
             'i Skiva 3.')

    def action_open_mail_rules(self):
        """Smartknapp (Min profil): öppna mina mail-regler."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Mina mail-regler',
            'res_model': 'user_mail_ai.rule',
            'view_mode': 'list,form',
            'domain': [('user_id', '=', self.id)],
        }
