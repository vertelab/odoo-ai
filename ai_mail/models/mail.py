from odoo import models, fields, api, _


class MailAlias(models.Model):
    _inherit = 'mail.alias'

    ai_quest = fields.Many2one('ai.quest', string="AI Quest")
    ai_agent = fields.Many2one('ai.agent', string="AI Agent")


class MailMessage(models.Model):
    _inherit = 'mail.message'

    ai_quest = fields.Many2one('ai.quest', string="AI Quest")
    ai_agent = fields.Many2one('ai.agent', string="AI Agent")