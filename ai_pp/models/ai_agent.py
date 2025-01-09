from odoo import models, api, fields, _

class AIAgent(models.Model):
    _inherit = "ai.agent"

    ai_type = fields.Selection(selection_add=[('perplex', 'Perplexity')], ondelete={'perplex': 'cascade'})
    
    
    
