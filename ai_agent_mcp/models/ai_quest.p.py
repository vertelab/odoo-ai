from odoo import models, fields, api


class AIQuestMCP(models.Model):
    _inherit = 'ai.quest'

    available_to_mcp = fields.Boolean(
        'Available to MCP',
        default=False,
        help="Make this quest available through Model Context Protocol"
    )

