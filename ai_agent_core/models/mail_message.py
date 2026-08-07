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
            # Botens egna partners (för loop-skydd) — deras meddelanden routas
            # inte. Inkluderar BÅDE medarbetarens partner OCH bot-användarens
            # partner (chat-initieringen), som kan vara olika.
            bot_partner_ids = {
                c.partner_id.id for c in channel.ai_coworker_ids
                if c.partner_id}
            for c in channel.ai_coworker_ids:
                for init in c.init_type_ids.filtered(
                        lambda i: i.init_type == 'chat' and i.chat_user_id):
                    if init.chat_user_id.partner_id:
                        bot_partner_ids.add(init.chat_user_id.partner_id.id)
            if rec.author_id and rec.author_id.id in bot_partner_ids:
                continue
            body_low = rec.body.lower()
            # DM (privat chat): hitta medarbetaren vars bot-partner är i kanalen
            if channel.channel_type == 'chat':
                member_partner_ids = set(
                    channel.channel_member_ids.mapped('partner_id').ids)
                for init in self.env['ai.coworker.init_type'].search([
                        ('init_type', '=', 'chat'),
                        ('enabled', '=', True),
                        ('chat_user_id', '!=', False)]):
                    bot = init.chat_user_id
                    if not (bot.partner_id
                            and bot.partner_id.id in member_partner_ids):
                        continue
                    # Loop-skydd: botens EGNA meddelanden routas inte
                    if rec.author_id and rec.author_id.id == bot.partner_id.id:
                        break
                    init.coworker_id._route_message(rec)
                    break
                continue
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
