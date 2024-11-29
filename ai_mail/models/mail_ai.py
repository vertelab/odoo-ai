# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

import logging

from odoo import api, fields, models, tools, SUPERUSER_ID
from odoo.exceptions import UserError
from odoo.tools.translate import _
from odoo.tools.misc import get_lang


_logger = logging.getLogger(__name__)

prompt_template = """
You are a deal analyzer. Based on the criteria below, determine if the email contains a "good deal":
- Substantial discount (40% or more).
- Value addition (e.g., "Buy One Get One Free" or additional bonuses).
- Significant price reduction compared to the market price.
- Urgency or exclusivity that makes the deal appealing.

If the deal is good, return call the create_lead function.
If the deal is not good, return "NOT A GOOD DEAL" and explain why in one sentence.

### Email Content:
{email}

### Response:
"""


class MailAI(models.Model):
    _name = "mail.ai"
    _description = "Mail managed by AI"
    _order = "id desc"
    _inherit = ['ai.agent', 'mail.thread.cc',
                'mail.thread.blacklist',
                'mail.activity.mixin',
                'utm.mixin',
                'format.address.mixin',
                ]
    _primary_email = 'email_from'
    _check_company_auto = True

    # Description
    name = fields.Char(
        'Opportunity', index='trigram', required=True,
        compute='_compute_name', readonly=False, store=True)
    user_id = fields.Many2one(
        'res.users', string='Salesperson', default=lambda self: self.env.user,
        domain="['&', ('share', '=', False), ('company_ids', 'in', user_company_ids)]",
        check_company=True, index=True, tracking=True)
    company_id = fields.Many2one(
        'res.company', string='Company', index=True,
        readonly=False, store=True)
    description = fields.Html('Notes')
    active = fields.Boolean('Active', default=True, tracking=True)

    mail_channel_id = fields.Many2one(
        'mail.channel.ai', string='Channel', readonly=True, store=True)

    color = fields.Integer('Color Index', default=0)

    # Customer / contact
    partner_id = fields.Many2one(
        'res.partner', string='Customer', check_company=True, index=True, tracking=10,
        help="Linked partner (optional). Usually created when converting the lead. You can find a partner by its "
             "Name, TIN, Email or Internal Reference.")

    contact_name = fields.Char(
        'Contact Name', tracking=30,
        readonly=False, store=True)
    partner_name = fields.Char(
        'Company Name', tracking=20,
        readonly=False, store=True,
        help='The name of the future partner company that will be created while converting the lead into opportunity')
    email_from = fields.Char(
        'Email', tracking=40, index='trigram',
        readonly=False, store=True)
    crm_lead_id = fields.Many2one('crm.lead', readonly=True)

    # ------------------------------------------------------------
    # MAILING
    # ------------------------------------------------------------
    def _notify_by_email_prepare_rendering_context(self, message, msg_vals=False, model_description=False,
                                                   force_email_company=False, force_email_lang=False):
        render_context = super()._notify_by_email_prepare_rendering_context(
            message, msg_vals, model_description=model_description,
            force_email_company=force_email_company, force_email_lang=force_email_lang
        )
        return render_context

    def _message_get_default_recipients(self):
        return {
            r.id: {
                'partner_ids': [],
                'email_to': ','.join(tools.email_normalize_all(r.email_from)) or r.email_from,
                'email_cc': False,
            } for r in self
        }

    @api.model
    def message_new(self, msg_dict, custom_values=None):
        """ Overrides mail_thread message_new that is called by the mailgateway
            through message_process.
            This override updates the document according to the email.
        """
        # remove default author when going through the mail gateway. Indeed we
        # do not want to explicitly set an user as responsible. We prefer that
        # assignment is done automatically (scoring) or manually. Otherwise it
        # would always be either root (gateway user) either alias owner (through
        # alias_user_id). It also allows to exclude portal / public users.
        self = self.with_context(default_user_id=False)

        if custom_values is None:
            custom_values = {}
        defaults = {
            'name': msg_dict.get('subject') or _("No Subject"),
            'email_from': msg_dict.get('from'),
            'partner_id': msg_dict.get('author_id', False),
        }

        if msg_dict.get('to'):
            mail_to = tools.email_normalize_all(msg_dict.get('to'))[-1]
            if mail_to:
                mail_channel_ai_id = self.env['mail.channel.ai'].search([
                    ('alias_name', '=', mail_to.split('@')[0])
                ])
                defaults['mail_channel_id'] = mail_channel_ai_id.id
        defaults.update(custom_values)

        return super(MailAI, self).message_new(msg_dict, custom_values=defaults)

    def _message_post_after_hook(self, message, msg_vals):
        if self.email_from and not self.partner_id:
            # we consider that posting a message with a specified recipient (not a follower, a specific one)
            # on a document without customer means that it was created through the chatter using
            # suggested recipients. This heuristic allows to avoid ugly hacks in JS.
            new_partner = message.partner_ids.filtered(
                lambda partner: partner.email == self.email_from or (
                        self.email_normalized and partner.email_normalized == self.email_normalized)
            )
            if new_partner:
                if new_partner[0].email_normalized:
                    email_domain = ('email_normalized', '=', new_partner[0].email_normalized)
                else:
                    email_domain = ('email_from', '=', new_partner[0].email)
                self.search([
                    ('partner_id', '=', False), email_domain, ('stage_id.fold', '=', False)
                ]).write({'partner_id': new_partner[0].id})
        return super(MailAI, self)._message_post_after_hook(message, msg_vals)

    def get_mail_activity(self):
        message_ids = self.message_ids
        ai_answer = self.mail_channel_id.ai_agent_id.create_agent(
            email=message_ids.mapped('body')[0]
        )
        raise UserError(ai_answer)

    def create_lead(self):
        lead_vals = {
            'name': self.name,
            'email_from': self.email_from,
            'mail_ai_id': self.id,
            'type': 'lead'
        }
        lead_id = self.env['crm.lead'].create(lead_vals)
        self.write({'crm_lead_id': lead_id.id})

    def action_show_crm_lead(self):
        return {
            'name': _('Lead'),
            'res_model': 'crm.lead',
            'view_mode': 'form',
            'res_id': self.crm_lead_id.id,
            'target': 'self',
            'type': 'ir.actions.act_window',
        }


