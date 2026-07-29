# -*- coding: utf-8 -*-
"""Mail message extensions for Buzz workspace."""

from odoo import models, fields, api


class MailMessage(models.Model):
    _inherit = 'mail.message'

    ai_buzz_internal = fields.Boolean(
        'Buzz Internal',
        help='True for agent-to-agent messages in a Buzz workspace. '
             'Can be filtered/collapsed in the UI.',
        default=False,
    )

    @api.model_create_multi
    def create(self, vals_list):
        internal = self.env.context.get('ai_buzz_internal', False)
        if internal:
            for vals in vals_list:
                vals['ai_buzz_internal'] = True
        return super(MailMessage, self).create(vals_list)
