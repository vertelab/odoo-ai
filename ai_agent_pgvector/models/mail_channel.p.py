import re
import markdown
from markupsafe import Markup
import markdownify
import logging
from langchain_core.messages import AIMessage

from odoo import models, fields, api, _
from odoo.tools.safe_eval import safe_eval
from odoo.exceptions import UserError, ValidationError
from odoo.tools.mail import html2plaintext

_logger = logging.getLogger(__name__)

class MailChannel(models.Model):
    # #if VERSION >= "17.0"
    _inherit = 'discuss.channel'
    # #elif VERSION <= "16.0"
    _inherit = 'mail.channel'
    # #endif

    @api.returns('mail.message', lambda value: value.id)
    def message_post(self, **kwargs):
        message = super(MailChannel, self).message_post(**kwargs)

        ai_quest = None
        if self.channel_type == "chat":
            # #if VERSION >= "15.0"
            ai_quest = self.env['res.users'].browse(self.channel_member_ids.mapped('partner_id.user_ids.id')).mapped(
                'ai_quest_id')
            # #elif VERSION <= "14.0"
            ai_quest = self.env['res.users'].browse(self.channel_last_seen_partner_ids.mapped('partner_id.user_ids.id')).mapped(
                'ai_quest_id')
            # #endif
            user = ai_quest.chat_user_id
        else:  # channel
            ai_quest = self.ai_quest_id
            user = self.env.ref('base.user_root')

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
                    
                    if ai_quest.use_feedback_history and ai_quest.feedback_llm:
                        message_id = self.with_user(user).message_post(
                            body=Markup(answer),
                            message_type='comment',
                            subtype_xmlid='mail.mt_comment',
                            parent_id=message.id,
                        )
                    else:
                        message_id = self.with_user(user).message_post(
                            body=Markup(answer),
                            message_type='comment',
                            subtype_xmlid='mail.mt_comment',
                        )
                    if _props and message_id:
                        self._postprocess_message_post(message_id, _props)
        return message
