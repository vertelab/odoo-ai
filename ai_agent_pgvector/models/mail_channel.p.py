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

    def send_ai_message(self,message):
        message_id = super(MailChannel,self).send_ai_message(message)
        _,ai_quest = self.get_user_and_quest()
        if message_id and ai_quest and ai_quest.use_feedback_history and ai_quest.feedback_llm:
            message_id.parent_id = message.id
        return message_id
