from odoo import models, api, fields, _


class AIAgent(models.Model):
    _inherit = "ai.agent"

    ai_type = fields.Selection(selection_add=[('e-avrop', 'E-avrop')], ondelete={'e-avrop': 'cascade'})
