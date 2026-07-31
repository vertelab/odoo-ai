# -*- coding: utf-8 -*-
"""AI Organization Key Results — OKR-style measurable outcomes."""

from odoo import models, fields, api, _


class AIOrgKeyResult(models.Model):
    _name = 'ai.org.key_result'
    _description = 'AI Organization Key Result'
    _order = 'sequence, id'
    _rec_name = 'name'

    goal_id = fields.Many2one('ai.org.goal', string='Goal',
        required=True, ondelete='cascade', index=True)
    name = fields.Char(required=True)
    description = fields.Text()

    sequence = fields.Integer(default=10)

    target_value = fields.Float('Target', required=True, default=100.0)
    current_value = fields.Float('Current', default=0.0)
    unit = fields.Char('Unit', default='%',
        help='t.ex. %, SEK, st, timmar')

    progress = fields.Float('Progress %', compute='_compute_progress',
        store=True)

    @api.depends('current_value', 'target_value')
    def _compute_progress(self):
        for kr in self:
            if kr.target_value:
                kr.progress = round(
                    (kr.current_value / kr.target_value) * 100.0, 1)
            else:
                kr.progress = 0.0
