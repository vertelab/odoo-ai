import re
from odoo import models, fields, api


class AIQuestMCP(models.Model):
    _inherit = 'ai.quest'

    available_to_mcp = fields.Boolean(
        'Available to MCP',
        default=False,
        help="Make this quest available through Model Context Protocol"
    )

    # MCP
    input_schema = fields.Text(
        string="Input Schema",
        help="JSON schema for the tool's input parameters, following the MCP specification.",
        copy=False,
    )
    tool_name = fields.Char(
        string="Tool Name",
        copy=False,
        help="Programmatic name for the tool. Auto-suggested, but can be edited.",
    )

    @api.onchange('name')
    def _onchange_name_set_tool_name(self):
        if self.name:
            self.tool_name = re.sub(r'[^a-zA-Z0-9_]', '', self.name.lower().replace(' ', '_'))
        else:
            self.tool_name = ''
    
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name') and not vals.get('tool_name'):
                vals['tool_name'] = re.sub(r'[^a-zA-Z0-9_]', '', vals['name'].lower().replace(' ', '_'))
        return super(AIQuestMCP, self).create(vals_list)