from odoo import models, fields, api, _


class CRMLead(models.Model):
    _inherit = "crm.lead"

    mail_ai_id = fields.Many2one('mail.ai', readonly=True)

    def action_show_mail_ai(self):
        return {
            'name': _('Mail AI'),
            'res_model': 'mail.ai',
            'view_mode': 'form',
            'res_id': self.mail_ai_id.id,
            'target': 'self',
            'type': 'ir.actions.act_window',
        }

