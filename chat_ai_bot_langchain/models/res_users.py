# -*- coding: utf-8 -*-
import logging
import time
import html

from langchain.schema import HumanMessage, SystemMessage, AIMessage
from langchain_openai import ChatOpenAI
from langchain.memory import ConversationBufferMemory
#from langchain_community.chat_models import ChatOpenAI
#from langchain.chat_models import ChatOpenAI

from odoo import models, fields, api, _

_logger = logging.getLogger(__name__)

# - This works...

class ResUsers(models.Model):
    _inherit = 'res.users'

    llm_type = fields.Selection(selection_add=[('langchain', "LangChain")], ondelete={'langchain': 'set default'})
    memory = ConversationBufferMemory()

    def langchain_run_ai_message_post(self, recipient, channel, author, message):
        with self.env.registry.cursor() as cr:
            try:
                env = api.Environment(cr, self.env.uid, self.env.context)
                user_id = env['res.users'].browse(recipient.id)
                channel_id = env['mail.channel'].browse(channel.id)

                chat = ChatOpenAI(openai_api_key=self.openai_api_key, temperature="0.8")

                #Let us test another approach:
 
                message = [
                    SystemMessage(content="Du heter Bettan och Du är en expert på Odoo."), #You are a covetous and jelous dragon.
                    HumanMessage(content=message)
                ]

              #  chat(
              #  [
              #  SystemMessage(content="You are an expert data scientist specialized with Odoo"), 
              #  HumanMessage(content="Explain this like I was five")
              #  ]
              #  )
                
                response = chat.invoke(message)
                _logger.info(f"\033[1;31m{response=}\033[0m")

                if isinstance(response, AIMessage) and hasattr(response, 'content'):
                    extracted_content = response.content
                    _logger.info(f"-----> GOT response.content...")
                else:
                    extracted_content = str(response)
                    _logger.info(f"-----> GOT str response....")
                
                extracted_content_html = extracted_content.replace(':', '<p>').replace('\n', '<br>')                

                memory = ConversationBufferMemory()
                memory.save_context({"input": "hi"}, {"output": "whats up"})

                channel_id.with_context(mail_create_nosubscribe=True).message_post(
                    body=f"<i>{extracted_content_html}</i>",
                    author_id=user_id.partner_id.id,
                    message_type='comment',
                    subtype_xmlid='mail.mt_comment'
                )
                
                env.cr.commit()
                return {}
            except Exception as e:
                _logger.error(f"{e}")