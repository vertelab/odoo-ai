# -*- coding: utf-8 -*-
import logging
import time
from odoo import models, fields, api, _

# Logger settings. In this module we set messages in green textcolor
_logger = logging.getLogger(__name__)
# Set textcolor into green, must be head in message. (gn for green)
magenta = "\033[35m"
# Reset Color to default, must be tail in message. (cr for color reset)
color_reset = "\033[0m"



class ResUsers(models.Model):
    _inherit = 'res.users'

    llm_type = fields.Selection(selection_add=[('openai', "OpenAI")], ondelete={'openai': 'set default'})

    def run_ai_message_post(self, recipient, channel, author, message):
        if self.llm_type != "openai":
            _logger.info(f"\033[35m M:OpenAI bot / F:res_users / C:ResUers / run_ai_message_post: NOT Open AI! \033[0m")
            #_logger.warning("Not Open ai run_ai_message_post "*100)
            #return super().run_ai_message_post(self, recipient, channel, author, message)
            return super(ResUsers, self).run_ai_message_post(recipient, channel, author, message)
        

        time.sleep(3)
        with self.env.registry.cursor() as cr:
            try:
                env = api.Environment(cr, self.env.uid, self.env.context)

                user_id = env['res.users'].browse(recipient.id)
                channel_id = env['mail.channel'].browse(channel.id)

                #openai_client = env['openai.thread'].openai_client_init(user_id)
                openai_client = env['openai.thread'].client_init(user_id)

                #thread = env['openai.thread'].sudo().openai_thread_init(openai_client, channel_id, user_id, author)
                thread = env['openai.thread'].sudo().thread_init(openai_client, channel_id, user_id, author)

                thread.add_message(openai_client, message, user_id)

                for msg in thread.wait4response(openai_client, user_id):
                    if not msg['role'] == 'user':
                        channel_id.with_context(mail_create_nosubscribe=True).message_post(
                            body=f"{msg['content']}",
                            author_id=user_id.partner_id.id,
                            message_type='comment',
                            subtype_xmlid='mail.mt_comment'
                        )
                env.cr.commit()
                return {}
            except Exception as e:
                #raise e
                _logger.error(e, exc_info=True)
