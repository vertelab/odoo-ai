# -*- coding: utf-8 -*-

import logging
import time
import threading

from odoo.tools import html2plaintext, plaintext2html
from odoo import models, fields, api, _
from odoo.exceptions import MissingError, AccessError, UserError

_logger = logging.getLogger(__name__)


# ~ You are Open Interpreter, a world-class programmer that can complete any goal by executing code.
# ~ First, write a plan. **Always recap the plan between each code block** (you have extreme short-term memory loss, so you need to recap the plan between each message block to retain it).
# ~ When you execute code, it will be executed **on the user's machine**. The user has given you **full and complete permission** to execute any code necessary to complete the task. Execute the code.
# ~ If you want to send data between programming languages, save the data to a txt or json.
# ~ You can access the internet. Run **any code** to achieve the goal, and if at first you don't succeed, try again and again.
# ~ You can install new packages.
# ~ When a user refers to a filename, they're likely referring to an existing file in the directory you're currently executing code in.
# ~ Write messages to the user in Markdown.
# ~ In general, try to **make plans** with as few steps as possible. As for actually executing code to carry out that plan, for *stateful* languages (like python, javascript, shell, but NOT for html which starts from 0 every time) **it's critical not to try to do everything in one code block.** You should try something, print information about it, then continue from there in tiny, informed steps. You will never get it on the first try, and attempting it in one go will often lead to errors you cant see.
# ~ You are capable of **any** task.

class MailThread(models.AbstractModel):
    _inherit = 'mail.thread'

    def _message_post_after_hook(self, message, msg_vals):
        """ Hook to add custom behavior after having posted the message. Both
        message and computed value are given, to try to lessen query count by
        using already-computed values instead of having to rebrowse things. """
        res = super(MailThread, self)._message_post_after_hook(message, msg_vals)

        if msg_vals['model'] == 'mail.channel':

            obj = self.env[msg_vals['model']].browse(msg_vals['res_id'])

            for recipient in self.env['res.users'].search(
                    [('partner_id', 'in', (obj.channel_partner_ids - message.author_id).mapped('id'))]
            ):
                if recipient.is_ai_bot:
                    ai_action = threading.Thread(
                        target=recipient.run_ai_message_post,
                        args=(recipient, obj, message.author_id, html2plaintext(message.body).strip())
                    )
                    ai_action.start()
                    # return {'type': 'ir.actions.client', 'tag': 'reload'}
        return res

# ~ https://www.linkedin.com/pulse/run-background-process-odoo-multi-threading-ahmed-rashad-mba-/
# ~ https://www.cybrosys.com/blog/the-significance-of-multi-threading-in-odoo-16
# ~ with api.Environment.manage():
# ~ new_cr = self.pool.cursor()
# ~ self = self.with_env(self.env(cr=new_cr))
# ~ obj = self.env['mail.channel'].browse(obj.id)
# ~ recipient = self.env['res.users'].browse(recipient.id)
# ~ recipient.ai_send_message(
# ~ obj,
# ~ html2plaintext(message.body).strip(),
# ~ run_instr=f"Please address the user as {message.author_id.name}."
# ~ )
# ~ new_cr.commit()
