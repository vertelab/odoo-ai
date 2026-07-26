# -*- coding: utf-8 -*-
"""ai.quest.builder.wizard — Quest Builder overlay wizard.

Opens as a large dialog from ai.quest form view.
Contains an iframe loading /ai/chat with the Quest Builder quest.
"""

from odoo import models, fields, api


class AIQuestBuilderWizard(models.TransientModel):
    _name = 'ai.quest.builder.wizard'
    _description = 'Quest Builder Wizard'

    quest_id = fields.Many2one(
        'ai.quest', string='Quest',
        help='Quest to build or edit. Empty = create new.')
    chat_url = fields.Char(compute='_compute_chat_url', string='Chat URL')

    @api.depends('quest_id')
    def _compute_chat_url(self):
        for wiz in self:
            # Find Quest Builder quest
            builder = self.env['ai.quest'].search(
                [('name', '=', 'Quest Builder')], limit=1)
            if not builder:
                wiz.chat_url = '/ai/chat?embedded=1'
                continue
            url = f'/ai/chat?quest_id={builder.id}&embedded=1'
            if wiz.quest_id:
                url += f'&context_quest={wiz.quest_id.id}'
            wiz.chat_url = url

    def action_open_builder(self):
        """Open the Quest Builder wizard for the current quest."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Quest Builder',
            'res_model': 'ai.quest.builder.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_quest_id': self.id,
            },
        }
