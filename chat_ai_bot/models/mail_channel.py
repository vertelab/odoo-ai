# -*- coding: utf-8 -*-

from odoo import models, _
from odoo.tools import html2plaintext, plaintext2html

COMMAND_AI = '/ai'

class MailChannel(models.Model):
    _inherit = 'mail.channel'


    def execute_command_ai(self, **kwargs):
        msg = _('Oops! Something went wrong!')

        partner = self.env.user.partner_id
        body = kwargs.get('body', '').strip()

        try:
            if not body or body == COMMAND_AI or not body.startswith(COMMAND_AI):
                msg = _('Ask something to the AI by simply typing "/ai "followed by the prompt. For example "/ai What is Odoo?"')
            else:
                msg = self._execute_command_ai(
                    partner=partner,
                    prompt=html2plaintext(body[len(COMMAND_AI):]).strip()
                )
        except Exception as e:
            msg = _('Oops! Something went wrong! %s') % str(e)

        # Send message with SuperUser (OdooBot)
        odoobot_id = self.env['ir.model.data']._xmlid_to_res_id("base.partner_root")
        self.with_context(mail_create_nosubscribe=True).sudo().message_post(
            body=msg,
            author_id=odoobot_id,
            message_type='comment',
            subtype_xmlid='mail.mt_comment'
        )


    def _execute_command_ai(self, partner, prompt):
        response = _('NO RESPONSE!! Please check settings')

        if self.env.user.odoogpt_chat_method == 'completion':
            response = self.env['odoogpt.openai.utils'].completition_create(
                prompt=self._execute_command_ai__build_prompt_completion(prompt)
            )
        elif self.env.user.odoogpt_chat_method == 'chat-completion':
            response = self.env['odoogpt.openai.utils'].chat_completion_create(
                messages=self._execute_command_ai__build_prompt_chat_completion(prompt)
            )

        return _("""{0} asked <i>\"{1}\"</i> <br /> {2}""").format(
            self._ping_partners(partner),
            prompt,
            plaintext2html(response)
        )

    def _execute_command_ai__build_prompt_completion(self, prompt):
        """Build the message to send to OpenAI Completition api"""
        return '{0}{1}{2}'.format(
            self.env.user.odoogpt_openai_prompt_prefix or '',
            prompt,
            self.env.user.odoogpt_openai_prompt_suffix or ''
        )
    _execute_command_ai__build_prompt = _execute_command_ai__build_prompt_completion    # unnecessary backward compatibility

    def _execute_command_ai__build_prompt_chat_completion(self, prompt):
        """Build the message to send to OpenAI Completition api"""
        return [
            {'role': 'system', 'content': self.env.user.odoogpt_chat_system_message},
            {'role': 'user', 'content': prompt},
        ]



    # UTILS

    def _ping_partners(self, partners):
        return '&nbsp;'.join("""<a href=\"{0}#model=res.partner&amp;id={1}\" class=\"o_mail_redirect\" data-oe-id=\"{1}\" data-oe-model=\"res.partner\" target=\"_blank\">@{2}</a>""".format(
            partner.get_base_url(), partner.id, partner.name
        ) for partner in partners)