# -*- coding: utf-8 -*-
##############################################################################
#
#    Copyright (C) 2015- 2019 Vertel AB (<http://www.vertel.se>).
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU Affero General Public License as
#    published by the Free Software Foundation, either version 3 of the
#    License, or (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU Affero General Public License for more details.
#
#    You should have received a copy of the GNU Affero General Public License
#    along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
##############################################################################

from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError
from odoo import tools
import uuid
import re
from passlib.hash import sha512_crypt

import logging
_logger = logging.getLogger(__name__)


class postfix_vacation_notification(models.Model):
    _name = 'postfix.vacation_notification'
    _description = "Postfix Vacation Notfication"
    
    user_id = fields.Many2one('res.users', 'User', ondelete='cascade', required=True,)
    notified = fields.Char('Notified', size=64, index=True)
    date = fields.Date('Date', default=fields.Date.context_today)


class postfix_alias(models.Model):
    _name = 'postfix.alias'
    _description = "Postfix Alias"
    
    user_id = fields.Many2one('res.users', ondelete='cascade', required=True)
    active = fields.Boolean('Active', default=True)
    mail = fields.Char(string='Complete Mail Address', size=64, compute='_onchange_mail',
                       help="Mail as <user>@<domain>, if you are using a foreign domain, make sure that this domain are "
                            "handled by the same mailserver", store=True)
    name = fields.Char(string='Mail Address', size=64,
                       help="Mail without domain (left side of @), leave blank for a catcall for the domain")
    
    @api.depends('user_id.company_id.domain', 'name')
    def _onchange_mail(self):
        for this in self:
            if this.name:
                this.mail = '%s@%s' % (this.name, this.user_id.company_id.domain)
            else:
                this.mail = '@%s' % (this.user_id.company_id.domain)


class res_users(models.Model):
    _inherit = 'res.users'
    
    _sql_constraints = [('postfix_mail_uniq', 'unique (postfix_mail)', 'The postfix mail address must be unique!'),
                        ('maildir_uniq', 'unique (maildir)', 'The maildir must be unique!')]
    
    def _remote_id(self):
        return str(uuid.uuid4())
    
    domain = fields.Char(related="company_id.domain", string='Domain', size=64, store=True, readonly=True)
    dovecot_password = fields.Char()
    forward_active = fields.Boolean('Foward Active', default=False)
    forward_address = fields.Text('Forward address', help='Comma separated list of mail addresses')
    forward_cp = fields.Boolean('Keep', help="Keep a local copy of forwarded messages")
    postfix_active = fields.Boolean('Postfix Active', default=False,)
    postfix_alias_ids = fields.One2many('postfix.alias', 'user_id', string='Alias', copy=False)
    postfix_mail = fields.Char(string="Real Mail Address", compute='_email', store=True)
    # remote_id = fields.Char(string='Remote ID', default=_remote_id, size=64, copy=False)
    remote_id = fields.Char(string='Remote ID', size=64, copy=False)
    spam_active = fields.Boolean('Spam Check', default=True)
    spam_killevel = fields.Selection(
        [
            ('10', 'Low (10)'),
            ('6', 'Medium (6)'),
            ('4.5', 'High (4.5)'),
            ('3', 'Very High (3)')],
        string='Spam Kill Level',
        help="Killed spam never reach your mail client",
        default='6')
    spam_tag2 = fields.Selection(
        [
            ('9.5', 'Low (9.5)'),
            ('5.5', 'Medium (5.5)'),
            ('4', 'High (4)'),
            ('2.5', 'Very High (2.5)')],
        string='Spam Tag Level two',
        help="Tagged spam will be marked as spam in your mail client and usually sorted in the spam directory",
        default='5.5')
    spam_tag = fields.Selection(
        [
            ('7.0', 'Low (7)'),
            ('3', 'Medium (3)'),
            ('1.5', 'High (1.5)'),
            ('0', 'Very high (0)')],
        string='Spam Tag Level',
        help="Tagged spam will be marked as spam in your mail client",
        default='3')
    vacation_subject = fields.Char('Subject', size=64,)
    vacation_text = fields.Text('Text', help="Vacation message for autorespond")
    vacation_active = fields.Boolean('Vacation Active', default=False)
    vacation_from = fields.Date('From', help="Vacation starts")
    vacation_to = fields.Date('To', help="Vacation ends")
    vacation_forward = fields.Char('Forward', size=64, help="Mailaddress to send messages during vacation")
    virus_active = fields.Boolean('Virus Check', default=True)
    quota = fields.Integer('Quota',)
    maildir = fields.Char(compute="_maildir_get", string='Maildir', size=64, store=True)
    email = fields.Char(help="Your e-mail address, Company or External")
    login = fields.Char(string="Email or login")
    login = fields.Char(help="External e-mail or login. Your Company e-mail address are constructed from login")

    USER_MAIL_FIELDS = ['name', 'login',
                        'dovecot_password',
                        'forward_active', 'forward_address', 'forward_cp',
                        'postfix_active', 'postfix_alias_ids', 'postfix_mail',
                        'quota',
                        'spam_active', 'spam_killevel', 'spam_tag2', 'spam_tag',
                        'vacation_subject', 'vacation_active', 'vacation_from',
                        'vacation_to', 'vacation_forward', 'vacation_text',
                        'virus_active',
                        ]
    
    @api.depends('company_id.domain', 'login')
    def _email(self):
        for this in self:
            if this.postfix_active and this.login:
                email_re = re.compile(r"""^([a-zA-Z0-9._%+-]+)@([a-zA-Z0-9.-]+\.[a-zA-Z]{2,6})$""", re.VERBOSE)
                if not email_re.match(this.login):  # login is not an email address
                    this.postfix_mail = '%s@%s' % (this.login, this.domain)
                elif email_re.match(this.login):    # login is an (external) email address, use only left part
                    this.postfix_mail = '%s@%s' % (email_re.match(this.login).groups()[0], this.company_id.domain)

    @api.depends('company_id.domain', 'login', 'postfix_mail')
    def _maildir_get(self):
        for this in self:
            if this.postfix_active and this.domain and this.postfix_mail:
                this.maildir = "%s/%s/" % (this.domain, this.postfix_mail)
                # this.alias_name = this.login
            else:
                this.maildir = None
                # this.alias_name = None
    
    # ~ def _check_credentials(self, password, env): #Used to debug dovecot password
        # ~ if self.dovecot_password:
           # ~ verify = sha512_crypt.verify(password, self.dovecot_password)
           # ~ _logger.warning(sha512_crypt.verify(password, self.dovecot_password))
           # ~ old_dovecote_password = self.dovecot_password
           # ~ #_logger.warning(f"{password=}")
           # ~ #_logger.warning(f"{self.dovecot_password=}")
           # ~ if verify:
               # ~ _logger.warning("The passwords match!")
        # ~ super(res_users, self)._check_credentials(password, env)


class res_company(models.Model):
    _inherit = 'res.company'
    _sql_constraints = [('domain_uniq', 'unique (domain)', 'The company domain must be unique!')]
    
    def _remote_id(self):
        return str(uuid.uuid4())
    
    remote_id = fields.Char(string='Remote ID', default=_remote_id, size=64)
    default_quota = fields.Integer(string='Default Quota per User', help="Quota in MB per user that is default",
                                   default=200)
    total_quota = fields.Integer(compute="_total_quota", string='All quota (MB)', help="Sum of all Users Quota in MB")
    catchall = fields.Char(compute='_catchall', string='Catchall', help="catchall mail address")
    domain = fields.Char(string='Domain', help="the internet domain for mail", store=True)
    nbr_users = fields.Integer(compute="_nbr_users", string="Nbr of users")
    
    def _set_domain_depricated(self):
        self.env['ir.config_parameter'].set_param('mail.catchall.domain', self.domain)
    
    @api.depends('name')
    def _get_domain_depricated(self):
        for rec in self:
            if rec.name:
                rec.domain = rec.env['ir.config_parameter'].get_param('mail.catchall.domain')
            else:
                rec.domain = False
    
    @api.depends('domain')
    def _catchall(self):
        for rec in self:
            if rec.domain:
                rec.catchall = 'catchall' + '@' + rec.domain
            else:
                rec.catchall = False
    
    def _total_quota(self):
        self.total_quota = sum(self.user_ids.filtered(lambda r: r.active is True and r.postfix_active is True).mapped('quota'))
    
    @api.depends('domain')
    def _email(self):
        for rec in self:
            if rec.domain:
                rec.email = 'info@%s' % rec.domain
            else:
                rec.email = False
    
    def _nbr_users(self):
        self.nbr_users = self.env['res.users'].sudo().search_count([('postfix_active', '=', True)])
