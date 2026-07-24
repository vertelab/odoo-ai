# -*- coding: utf-8 -*-
"""ir.actions.server — add powerbox_quest_id for auto-binding."""

from odoo import models, fields


class IrActionsServer(models.Model):
    _inherit = 'ir.actions.server'

    powerbox_quest_id = fields.Many2one(
        'ai.quest', string='Powerbox Quest',
        ondelete='cascade',
        help='The powerbox quest that auto-created this action. '
             'Managed automatically — do not edit manually.')
