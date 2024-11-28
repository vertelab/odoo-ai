import logging

from odoo import api, exceptions, fields, models, _


_logger = logging.getLogger(__name__)


class MailAIChannel(models.Model):
    _name = "mail.channel.ai"
    _inherit = ['mail.alias.mixin', 'mail.thread']
    _description = "Mail Channel"

    name = fields.Char('Sales Team', required=True, translate=True)
    sequence = fields.Integer('Sequence', default=10)
    active = fields.Boolean(default=True,
                            help="If the active field is set to false, it will allow you to hide the Sales Team "
                                 "without removing it.")
    company_id = fields.Many2one(
        'res.company', string='Company', index=True,
        default=lambda self: self.env.company)

    ai_agent_id = fields.Many2one('ai.agent', string="AI Agent")

    ai_model_id = fields.Many2one('ai.agent.llm', string="AI Agent Model", related="ai_agent_id.ai_agent_model_id")

    alias_id = fields.Many2one(
        'mail.alias', string='Alias', ondelete="restrict", required=True,
        help="The email address associated with this channel. New emails received will automatically create new leads "
             "assigned to the channel.")

    # alias: improve fields coming from _inherits, use inherited to avoid replacing them
    alias_user_id = fields.Many2one(
        'res.users', related='alias_id.alias_user_id', readonly=False, inherited=True,
        domain=lambda self: [('groups_id', 'in', self.env.ref('sales_team.group_sale_salesman_all_leads').id)])

    # def write(self, vals):
    #     result = super(MailAIChannel, self).write(vals)
    #     for rec in self:
    #         alias_vals = rec._alias_get_creation_values()
    #         rec.write({
    #             'alias_name': alias_vals.get('alias_name', rec.alias_name),
    #             'alias_defaults': alias_vals.get('alias_defaults'),
    #         })
    #     return result

    def _alias_get_creation_values(self):
        values = super(MailAIChannel, self)._alias_get_creation_values()
        values['alias_model_id'] = self.env['ir.model']._get('mail.ai').id
        return values



