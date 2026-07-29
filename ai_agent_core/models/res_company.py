# -*- coding: utf-8 -*-
"""res.company — Company memory integration."""

from odoo import models, fields, api
import logging

_logger = logging.getLogger(__name__)


class ResCompany(models.Model):
    _inherit = 'res.company'

    # ── Company Identity (ported from ai_agent) ──
    company_mission = fields.Html(
        'Our Mission',
        help='The company mission statement. Displayed to AI agents '
             'as context for decision-making.')
    company_values = fields.Html(
        'Our Values',
        help='The company values. Displayed to AI agents '
             'as context for decision-making.')
    company_mission_last_review = fields.Datetime('Mission Last Reviewed')
    company_values_last_review = fields.Datetime('Values Last Reviewed')

    # ── Company Memory ──
    company_memory_ids = fields.One2many(
        'ai.company.memory', 'company_id',
        string='Company Memories',
        help='All company memories.')
    company_memory_count = fields.Integer(
        string='Memory Count',
        compute='_compute_company_memory_count')

    @api.depends('company_memory_ids')
    def _compute_company_memory_count(self):
        for r in self:
            r.company_memory_count = len(r.company_memory_ids)

    def action_open_company_memory(self):
        """Smart button: öppna företagets minnen."""
        self.ensure_one()
        return {
            'name': 'Company Memories',
            'type': 'ir.actions.act_window',
            'res_model': 'ai.company.memory',
            'view_mode': 'list,form',
            'target': 'current',
            'domain': [('company_id', '=', self.id)],
            'context': {'default_company_id': self.id},
        }
