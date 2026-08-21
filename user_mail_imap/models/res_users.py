from odoo import models, fields, api
from cryptography.fernet import Fernet

import logging
_logger = logging.getLogger(__name__)


class ResUsers(models.Model):
    _inherit = 'res.users'

    imap_password = fields.Char(string='IMAP Password', help='Encrypted IMAP password')

    imap_poll_enabled = fields.Boolean(
        string='Aktivera mail-pollning',
        default=False,
        help='Pollerar denna användares brevlåda via IMAP (cron).')
    last_imap_sync = fields.Datetime(
        string='Senaste IMAP-synk',
        help='Tidpunkt för senaste lyckade pollning (inkrementell hämtning).')

    def _get_encryption_key(self):
        param = self.env['ir.config_parameter'].sudo()
        key = param.get_param('user_mail_imap.encryption_key')
        if not key:
            key = Fernet.generate_key().decode()
            param.set_param('user_mail_imap.encryption_key', key)
        return key

    def _encrypt_imap_pw(self, password):
        if not password:
            return False
        f = Fernet(self._get_encryption_key().encode())
        return f.encrypt(password.encode()).decode()

    def _decrypt_imap_pw(self):
        if not self.imap_password:
            return None
        key = self._get_encryption_key()
        if not key:
            return None
        f = Fernet(key.encode())
        try:
            return f.decrypt(self.imap_password.encode()).decode()
        except Exception:
            return None

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('password'):
                vals['imap_password'] = self._encrypt_imap_pw(vals['password'])
        return super().create(vals_list)

    def write(self, values):
        if values.get('password'):
            values['imap_password'] = self._encrypt_imap_pw(values['password'])
        return super().write(values)
