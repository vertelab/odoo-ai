# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.
import re
import markdown
from markupsafe import Markup
import markdownify
import logging
from langchain_core.messages import AIMessage
from odoo import api, fields, models, tools, _
from odoo.exceptions import ValidationError, UserError
from odoo.tools.mail import html2plaintext


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
        self.send_ai_message(message)
        return message

    def get_user_and_quest(self):
        ai_quest = False
        if self.channel_type == "chat":
            ai_quest = self.env['res.users'].browse(self.channel_member_ids.mapped('partner_id.user_ids.id')).mapped(
                'ai_quest_id')
            user = ai_quest.chat_user_id
        else:  # channel
            ai_quest = self.ai_quest_id
            user = self.env.ref('base.user_root')
        return user, ai_quest


    def send_ai_message(self,message):

        user, ai_quest = self.get_user_and_quest()
        message_id = False

        if message.author_id != user.partner_id:
            if ai_quest and self._continue_with_chat(ai_quest, message):
                bot_response = ai_quest.with_user(self.env.user).chat(message, self, user)
                _logger.error(f"{bot_response=}")

                if bot_response:
                    answer = _('no answer')

                    message_content, _props = self._process_message_post(bot_response)
                    if message_content:
                        if ai_quest.debug:
                            answer = markdown.markdown(message_content)
                        else:
                            answer = re.sub(
                                r'<think>.*?</think>', '', markdown.markdown(message_content),
                                flags=re.DOTALL
                            )

                    message_id = self.with_user(user).message_post(
                        body=Markup(answer),
                        message_type='comment',
                        subtype_xmlid='mail.mt_comment',
                    )
                    if _props and message_id:
                        self._postprocess_message_post(message_id, _props)
                    
        return message_id
        

    def _continue_with_chat(self, ai_quest, message):
        if not ai_quest.allow_trigger_words:
            return True

        message_body = html2plaintext(message.body)
        trigger_words = ai_quest.chat_trigger_words.split(',')

        if ai_quest.allow_trigger_words and any(word.strip().lower() in message_body.lower() for word in trigger_words):
            return True
        return False

    def _process_message_post(self, bot_response):
        message_content = _('no answer')

        if bot_response.get('response', False):
            messages = bot_response.get('response', {}).get('messages', [])
        else:
            messages = bot_response.get('result', {}).get('messages', [])
        ai_messages = [m for m in messages if isinstance(m, AIMessage)]
        last_ai_message = ai_messages[-1] if len(ai_messages) != 0 else None

        if messages and last_ai_message:
            # Store the content before processing
            message_content = last_ai_message.content
        return message_content, None


    def _postprocess_message_post(self, message_id, _):
        pass