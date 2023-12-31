# -*- coding: utf-8 -*-
import logging
from odoo import models, fields, api, _

_logger = logging.getLogger(__name__)

class ResUsers(models.Model):
    _inherit = 'res.users'

    #TODO driver type so we can install several and tie a bot to it
    is_ai_bot = fields.Boolean(string="Is an AI-Bot")
    openai_api_key = fields.Char(required=False)
    openai_base_url = fields.Char(required=False)
    openai_model = fields.Char(required=False)
    openai_max_tokens = fields.Integer(required=False)
    openai_temperature = fields.Float(required=False)
    # ~ chat_method = fields.Selection(required=False)
    chat_system_message = fields.Text(required=False)
    openai_prompt_prefix = fields.Char(required=False)
    openai_prompt_suffix = fields.Char(required=False)
    openai_assistant_name = fields.Char(required=False)
    openai_assistant_instructions = fields.Text(required=False)
    openai_assistant_tools = fields.Json(required=False)
    openai_assistant_model = fields.Char(required=False)
    openai_assistant = fields.Char(required=False)
    
    def ai_message_post(self,channel,author,message):

        # ~ openai_client = self.env['openai.thread'].client_init(self)
        openai_client = None
        thread = self.env['openai.thread'].thread_init(openai_client,channel,self,author)        

        #TODO Announce this command move to JS/controller
        if message == '/reset':
            for msg in thread.thread_unlink(openai_client,channel):
                if not msg['role'] == 'user':
                    channel.with_context(mail_create_nosubscribe=True).sudo().message_post(
                        body=f"{msg['content']}",
                        author_id=self.partner_id.id,
                        message_type='comment',
                        subtype_xmlid='mail.mt_comment'
                )
          
        thread.add_message(openai_client,message)
        for msg in thread.wait4response(openai_client):
            if not msg['role'] == 'user':
                channel.with_context(mail_create_nosubscribe=True).sudo().message_post(
                    body=f"{msg['content']}",
                    author_id=self.partner_id.id,
                    message_type='comment',
                    subtype_xmlid='mail.mt_comment'
                )
