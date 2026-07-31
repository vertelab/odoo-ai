# -*- coding: utf-8 -*-
"""hr.employee extension — AI-medarbetare i samma vy som människor."""

from odoo import models, fields, api


class HrEmployee(models.Model):
    _inherit = 'hr.employee'

    is_ai = fields.Boolean(
        'AI-medarbetare',
        default=False,
        help='True om detta är en AI-medarbetare (ai.coworker). '
             'Används för att filtrera och särskilja i vyer.')

    ai_coworker_id = fields.Many2one(
        'ai.coworker', string='AI Coworker',
        help='Motswarande ai.coworker om denna employee är en AI.')

    # Överskugga name för att visa 🤖-prefix i vyer
    def name_get(self):
        res = []
        for emp in self:
            name = emp.name or ''
            if emp.is_ai:
                # Check heartbeat status for indicator
                coworker = emp.ai_coworker_id
                if coworker and coworker.heartbeat_enabled:
                    name = f'🤖 {name}'
                else:
                    name = f'🤖 {name}'
            res.append((emp.id, name))
        return res
