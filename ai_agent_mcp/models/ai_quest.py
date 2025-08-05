from odoo import models, fields, api


class AIQuestMCP(models.Model):
    _inherit = 'ai.quest'

    available_to_mcp = fields.Boolean(
        'Available to MCP',
        default=False,
        help="Make this quest available through Model Context Protocol"
    )

    # @api.model
    # def get_mcp_available_quests(self):
    #     """Return quests available for MCP"""
    #     return self.search([('available_to_mcp', '=', True)])
    #
    # def to_mcp_resource(self):
    #     """Convert quest to MCP resource format"""
    #     self.ensure_one()
    #     return {
    #         'uri': f'/{self.env.cr.dbname}/mcp/quest/{self.id}',
    #         'name': self.name,
    #         'description': self.sub_description or f'AI Quest: {self.name}',
    #         'mimeType': 'application/json'
    #     }
    #
    # def execute_via_mcp(self, prompt=None, record_id=None, model=None):
    #     """Execute quest via MCP with parameter validation"""
    #     self.ensure_one()
    #
    #     if not self.available_to_mcp:
    #         raise ValueError('Quest not available via MCP')
    #
    #     # Check access rights
    #     self.check_access_rights('read')
    #     self.check_access_rule('read')
    #
    #     # Get record if specified
    #     record = None
    #     if record_id and model:
    #         if model in self.env:
    #             record = self.env[model].browse(record_id)
    #             if not record.exists():
    #                 raise ValueError(f'Record {record_id} not found in model {model}')
    #         else:
    #             raise ValueError(f'Model {model} not found')
    #
    #     # Execute quest with original interface
    #     result = self.run(prompt=prompt, record=record)
    #     return result