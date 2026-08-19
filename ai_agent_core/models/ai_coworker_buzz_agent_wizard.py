# -*- coding: utf-8 -*-
"""Wizard for creating a new Buzz agent from a quest."""

from odoo import models, fields, api


class AICoworkerBuzzAgentWizard(models.TransientModel):
    _name = 'ai.coworker.buzz.agent.wizard'
    _description = 'Create Buzz Agent'

    coworker_id = fields.Many2one('ai.coworker', string='Quest', required=True)
    topic = fields.Char('Topic / Role', required=True,
        help='What the new agent should handle, e.g. "Swedish VAT"')

    def action_create(self):
        self.ensure_one()
        result = self.coworker_id._buzz_suggest_or_create_agent(self.topic)
        agent = result.get('agent') or result.get('suggested')
        if not agent:
            return {'type': 'ir.actions.act_window_close'}
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'ai.agent',
            'res_id': agent.id,
            'view_mode': 'form',
            'views': [[False, 'form']],
            'target': 'current',
        }
