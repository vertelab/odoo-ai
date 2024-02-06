# -*- coding: utf-8 -*-
import logging
import time
from odoo import models, fields, api, _

_logger = logging.getLogger(__name__)


class ResUsers(models.Model):
    _inherit = 'res.users'

    llm_type = fields.Selection(selection_add=[('openai', "OpenAI")], ondelete={'openai': 'set default'})

    def openai_run_ai_message_post(self, recipient, channel, author, message):
        _logger.error("openai_run_ai_message_post second part of chain of functions")
        _logger.error(f"{self=}, {recipient=}, {channel=}, {author=}, {message=}")
        time.sleep(3)
        with self.env.registry.cursor() as cr:
            try:
                env = api.Environment(cr, self.env.uid, self.env.context)

                user_id = env['res.users'].browse(recipient.id)
                channel_id = env['mail.channel'].browse(channel.id)

                openai_client = env['openai.thread'].openai_client_init(user_id)

                thread = env['openai.thread'].sudo().openai_thread_init(openai_client, channel_id, user_id, author)
                thread.add_message(openai_client, message)

                for msg in thread.wait4response(openai_client):
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
                _logger.error(f"{e}")
