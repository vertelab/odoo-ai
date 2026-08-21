from odoo import models, fields, api


class ImapPasswordWizard(models.TransientModel):
    _name = 'user.mail.imap.password.wizard'
    _description = 'Set IMAP Password'

    password = fields.Char(string='IMAP Password', required=True)

    def action_save(self):
        self.env.user.sudo().imap_password = self.env.user._encrypt_imap_pw(self.password)
        return {'type': 'ir.actions.act_window_close'}
