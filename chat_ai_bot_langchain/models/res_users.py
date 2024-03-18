import logging, time, html, json
from langchain.schema import HumanMessage, SystemMessage, AIMessage
from langchain_openai import ChatOpenAI
from odoo import models, fields, api, _

_logger = logging.getLogger(__name__)

class ResUsers(models.Model):
    _inherit = 'res.users'

    llm_type = fields.Selection(selection_add=[('langchain', "LangChain 🦜")], ondelete={'langchain': 'set default'})

    def run_ai_message_post(self, recipient, channel, author, message):
        if self.llm_type != "langchain":
            return super(ResUsers, self).run_ai_message_post(recipient, channel, author, message)
        
        _logger.info(f"Waiting some seconds...")            
        time.sleep(3)
        
        with self.env.registry.cursor() as cr:
            try:
                env = api.Environment(cr, self.env.uid, self.env.context)
                user_id = env['res.users'].browse(recipient.id)
                channel_id = env['mail.channel'].browse(channel.id)
                openai_client = env['openai.thread'].client_init(user_id)
                thread = env['openai.thread'].sudo().thread_init(openai_client, channel_id, user_id, author)
                thread.add_message(openai_client, message, user_id, role="user")
                response = thread.wait4response(openai_client, user_id)

                _logger.info(f"\033[3;36m\n"
                    f"    Response from LLM:\033[0m\033[3;36m   {response}\033[0m")
        
                channel_id.with_context(mail_create_nosubscribe=True).message_post(
                    body=f"<i>{response}</i>",
                    author_id=user_id.partner_id.id,
                    message_type='comment',
                    subtype_xmlid='mail.mt_comment'
                )
              
                env.cr.commit()
                return {}
            except Exception as e:
                _logger.error(e, exc_info=True)
