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
        ctx = self.env.context
        internal = ctx.get('ai_buzz_internal', False)
        if internal:
            for vals in vals_list:
                vals['ai_buzz_internal'] = True
        records = super(MailMessage, self).create(vals_list)

        # @mention-router: kolla om meddelandet innehåller @alias
        for rec in records:
            if rec.model != 'discuss.channel' or not rec.body:
                continue
            if rec.author_id and rec.author_id.user_ids:
                continue  # Skip messages from real users to avoid loops
            channel = self.env['discuss.channel'].browse(rec.res_id)
            if not channel.exists():
                continue
            for coworker in channel.ai_coworker_ids:
                if not coworker.channel_alias:
                    continue
                if f'@{coworker.channel_alias}' in rec.body:
                    coworker._route_message(rec)
                    break  # Only route to first matching coworker

        return records
