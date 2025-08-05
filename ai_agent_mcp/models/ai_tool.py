from odoo import models, fields

class AITool(models.Model):
    _inherit = "ai.tool"

    available_to_mcp = fields.Boolean(string="Available to MCP", default=False)