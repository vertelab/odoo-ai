# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.


import logging


from odoo import api, fields, models, tools, _, Command
from odoo.exceptions import ValidationError, UserError


_logger = logging.getLogger(__name__)


class MailMessage(models.Model):
    _inherit = 'mail.message'

    ai_quest_session_id = fields.Many2one(comodel_name='ai.quest.session', string="Session", help="")


class MailChannel(models.Model):
    _inherit = 'mail.channel'

    ai_quest_id = fields.Many2one(comodel_name='ai.quest', string="Quest", help="")
    ai_quest_session_id = fields.Many2one(comodel_name='ai.quest.session', string="Session", help="")

    @api.returns('mail.message', lambda value: value.id)
    def message_post(self, **kwargs):
        message = super(MailChannel, self).message_post(**kwargs)

        # Check if the message is from a user (not the bot itself)
        _logger.warning(f"{message.author_id=} {message.parent_id=} {self.ai_quest_id=}")
        if message.author_id != self.env.ref('base.partner_root'):
            if self.ai_quest_id:
                bot_response = self.ai_quest_id.chat(message)
                _logger.error(f"{bot_response=}")
                if bot_response:
                    self.with_user(self.env.ref('base.user_root')).message_post(
                        body=bot_response,
                        message_type='comment',
                        subtype_xmlid='mail.mt_comment',
                    )
        return message

