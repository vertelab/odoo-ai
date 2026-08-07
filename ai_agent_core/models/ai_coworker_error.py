# -*- coding: utf-8 -*-
"""ai.coworker.error — fel-loggsystem för AI-medarbetare.

Fel som uppstår under körningar (verktyg, provider, max_rounds) loggas på
sessionen så de kan triageras och lösas vart efter.
"""

from odoo import models, fields


class AICoworkerError(models.Model):
    _name = 'ai.coworker.error'
    _description = 'AI Coworker Error'
    _order = 'create_date desc, id desc'

    session_id = fields.Many2one(
        'ai.coworker.session', string='Session',
        ondelete='cascade', index=True)
    coworker_id = fields.Many2one(
        'ai.coworker', string='AI Medarbetare',
        related='session_id.coworker_id', store=True, index=True)

    error_type = fields.Selection([
        ('tool_error', 'Verktygsfel'),
        ('search_error', 'Sökfel'),
        ('provider_error', 'Provider-fel'),
        ('max_rounds', 'Max rounds'),
        ('other', 'Annat'),
    ], string='Feltyp', default='other')
    tool_name = fields.Char('Verktyg')
    message = fields.Text('Meddelande')
    context = fields.Text('Kontext')
    resolved = fields.Boolean('Löst', default=False)
    resolution = fields.Text('Lösning/Notering')
    create_date = fields.Datetime('Uppstod', readonly=True)

    def action_mark_resolved(self):
        self.write({'resolved': True})
        return True
