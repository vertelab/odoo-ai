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

        # @mention-router: kolla om meddelandet innehåller @alias ELLER
        # medarbetarens/partnerns namn (normaliserat) — så "@allmän assistent"
        # och "@allmn" båda fungerar. Riktiga användare routas; botens EGNA
        # meddelanden hoppas över (loop-skydd).
        for rec in records:
            if rec.model != 'discuss.channel' or not rec.body:
                continue
            channel = self.env['discuss.channel'].browse(rec.res_id)
            if not channel.exists():
                continue
            # Botens egna partners (för loop-skydd) — deras meddelanden routas inte
            bot_partner_ids = {
                c.partner_id.id for c in channel.ai_coworker_ids
                if c.partner_id}
            if rec.author_id and rec.author_id.id in bot_partner_ids:
                continue
            body_low = rec.body.lower()
            for coworker in channel.ai_coworker_ids:
                mentions = []
                if coworker.channel_alias:
                    mentions.append(f'@{coworker.channel_alias}')
                if coworker.name:
                    mentions.append('@' + coworker.name.lower())
                if coworker.partner_id and coworker.partner_id.name:
                    mentions.append('@' + coworker.partner_id.name.lower())
                if any(m in body_low for m in mentions if m):
                    coworker._route_message(rec)
                    break  # Only route to first matching coworker

        return records
