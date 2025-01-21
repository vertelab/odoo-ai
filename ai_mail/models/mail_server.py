from odoo import models, fields, api, _


class MailServer(models.Model):
    _inherit = 'fetchmail.server'

    ai_quest = fields.Many2one('ai.quest', string="AI Quest")
    ai_agent = fields.Many2one('ai.agent', string="AI Agent")
