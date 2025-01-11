# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.


import logging

import markdown

from langchain_core.messages import AIMessage
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

        ai_quest = None
        if self.is_chat:
            ai_quest = self.env['res.users'].browse(self.channel_member_ids.mapped('partner_id.user_ids.id')).mapped(
                'ai_quest_id')
            user = ai_quest.chat_user_id
        else:  # channel
            ai_quest = self.ai_quest_id
            user = self.env.ref('base.user_root')

        if message.author_id != user.partner_id:
            if ai_quest:            # use the AI as in logged user
                bot_response = ai_quest.with_user(self.env.user).chat(message,self,user)
                _logger.error(f"{bot_response=}")
                if bot_response:  # Answer as the user the bot is
                    answer = _('no answer')
                    if bot_response.get('messages'):
                        answer = '<br/>'.join(m.content for m in bot_response['messages'][1:])
                    elif bot_response.get('result'):
                        if not isinstance(bot_response['result'], AIMessage):
                            answer = '<br/>'.join(m.content for m in bot_response['result']['messages'][1:])
                        else:
                            answer = bot_response['result'].content
                    self.with_user(user).message_post(
                        body=markdown.markdown(answer),
                        message_type='comment',
                        subtype_xmlid='mail.mt_comment',
                    )
        return message
