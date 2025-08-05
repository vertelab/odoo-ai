from fastapi import APIRouter
from odoo import api, fields, models
from odoo.api import Environment

from odoo.addons.fastapi.dependencies import odoo_env

class FastapiEndpoint(models.Model):
    _inherit = "fastapi.endpoint"

    app: str = fields.Selection(
        selection_add=[
            ("ai_agent_mcp", "AI Agent MCP")
        ],
        ondelete={"ai_agent_mcp": "cascade"}
    )

    def _get_fastapi_routers(self):
        if self.app == "ai_agent_mcp":
            return [ai_agent_router]
        return super()._get_fastapi_routers()

ai_agent_router = APIRouter(prefix="/mcp", tags=["AI Agent"])
