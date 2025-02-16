# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

from langchain_core.messages import AIMessage
from markupsafe import Markup
from odoo import api, fields, models, tools, _
from odoo.exceptions import ValidationError, UserError
import logging
import markdown
import re


_logger = logging.getLogger(__name__)


class MailMessage(models.Model):
    _inherit = 'mail.message'

    ai_quest_session_id = fields.Many2one(comodel_name='ai.quest.session', string="Session", help="")


class MailChannel(models.Model):
    # #if VERSION >= "17.0"
    _inherit = 'discuss.channel'
    # #elif VERSION <= "16.0"
    _inherit = 'mail.channel'
    # #endif

    ai_quest_id = fields.Many2one(comodel_name='ai.quest', string="Quest", help="")
    ai_quest_session_id = fields.Many2one(comodel_name='ai.quest.session', string="Session", help="")

    @api.returns('mail.message', lambda value: value.id)
    def message_post(self, **kwargs):
        message = super(MailChannel, self).message_post(**kwargs)

        ai_quest = None
        if self.channel_type == "chat":
            ai_quest = self.env['res.users'].browse(self.channel_member_ids.mapped('partner_id.user_ids.id')).mapped(
                'ai_quest_id')
            user = ai_quest.chat_user_id
        else:  # channel
            ai_quest = self.ai_quest_id
            user = self.env.ref('base.user_root')

        if message.author_id != user.partner_id:
            if ai_quest:  # use the AI as in logged user
                bot_response = ai_quest.with_user(self.env.user).chat(message, self, user)
                _logger.error(f"{bot_response=}")
                if bot_response:  # Answer as the user the bot is
                    answer = _('no answer')

                    if bot_response.get('response', False):
                        messages = bot_response.get('response', {}).get('messages', [])
                    else:
                        messages = bot_response.get('result', {}).get('messages', [])
                    ai_messages = [m for m in messages if isinstance(m, AIMessage)]
                    last_ai_message = ai_messages[-1] if len(ai_messages) != 0 else None

                    if messages and last_ai_message:
                        _logger.error(f"{last_ai_message=}")
                        cleaned_text = last_ai_message.content
                        if not ai_quest.debug: # Think is from some reson models eg DeepSeek
                            cleaned_text = re.sub(r'<think>.*?</think>', '', cleaned_text, flags=re.DOTALL)
                        # Ta bort tomma <p>-taggar som kan ha blivit kvar
                        cleaned_text = re.sub(r'<p>\s*</p>', '', cleaned_text)
                        answer = markdown.markdown(cleaned_text)

                    self.with_user(user).message_post(
                        body=Markup(answer),
                        message_type='comment',
                        subtype_xmlid='mail.mt_comment',  # Ev problem i Odoo 14
                    )
        return message
