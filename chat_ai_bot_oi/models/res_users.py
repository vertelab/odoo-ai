# -*- coding: utf-8 -*-
import logging
from odoo import models, fields, api, _
import time

_logger = logging.getLogger(__name__)

class ResUsers(models.Model):
    _inherit = 'res.users'

    llm_type = fields.Selection(selection_add=[('open_interpreter', "Open Interpreter")], ondelete={'open_interpreter': 'set default'})

    def run_ai_message_post(self, recipient, channel, author, message):
        if self.llm_type != "open_interpreter":
            _logger.info(f"\033[1;32m Run AI Message Post got that Open Intepreter is NOT set.\033[0m")
           # _logger.error("Not Open Interpreter run_ai_message_post "*100)
            return super().run_ai_message_post(recipient, channel, author, message)
            #return super(ResUsers, self).run_ai_message_post(recipient, channel, author, message)
        
        time.sleep(3)
        with self.env.registry.cursor() as cr:
            try:
                env = api.Environment(cr, self.env.uid, self.env.context)

                user_id = env['res.users'].browse(recipient.id)
                channel_id = env['mail.channel'].browse(channel.id)

                client = env['openai.thread'].client_init(user_id) # ------

                thread = env['openai.thread'].sudo().thread_init(client, channel_id, user_id, author)
                thread.add_message(client, message, user_id)

                for msg in thread.wait4response(client, user_id):
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
                _logger.error(e, exc_info=True)
