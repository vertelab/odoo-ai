from odoo import models, api, fields, _


class AIAgent(models.Model):
    _inherit = "ai.agent"

    type = fields.Selection(selection_add=[()])
