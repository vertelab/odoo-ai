import logging
from typing import List, TYPE_CHECKING
from fastapi import FastAPI, Depends
from fastapi_mcp import FastApiMCP
from fastapi_mcp.types import AuthConfig

from odoo import api, fields, models, SUPERUSER_ID
from odoo.modules.registry import Registry
from odoo.http import request
from odoo.addons.ai_agent_mcp.controllers.main import ai_agent_router
from ..controllers.main import ai_agent_router
from ..services.mcp_tools import create_dynamic_quest_router
from ..security.auth import verify_api_key

# Conditional import for APIRouter to avoid circular dependency during type checking
if TYPE_CHECKING:
    from fastapi import APIRouter

_logger = logging.getLogger(__name__)

# This will hold the MCP server instance once created.
mcp_server = None

class FastapiEndpoint(models.Model):
    _inherit = "fastapi.endpoint"

    app: str = fields.Selection(
        selection_add=[
            ("ai_agent_mcp", "AI Agent MCP")
        ],
        ondelete={"ai_agent_mcp": "cascade"}
    )

    def _get_fastapi_routers(self) -> List["APIRouter"]:
        """Return the api routers to use for the instance."""
        if self.app == "ai_agent_mcp":

            return [ai_agent_router]
        return super()._get_fastapi_routers()

    def _get_app(self) -> FastAPI:
        """Override to add FastMCP integration for ai_agent_mcp app type."""
        if self.app == "ai_agent_mcp":
            return self._get_mcp_enabled_app()
        return super()._get_app()

    def _get_mcp_enabled_app(self) -> FastAPI:
        """Builds and returns the complete FastAPI app with dynamic MCP tools."""
        global mcp_server
        _logger.info("Building AI Agent MCP FastAPI app...")

        app = FastAPI(
            title="AI Agent Base API",
            version="1.0.0",
        )

        app.include_router(ai_agent_router)

        quest_app = FastAPI(
            title="AI Quest MCP Tools",
            description="Dynamically generated API endpoints for each AI Quest.",
        )

        dynamic_router = create_dynamic_quest_router()
        quest_app.include_router(dynamic_router, prefix="/quests")

        # Create the MCP server instance and store it globally
        # Configure it with our API key authentication
        mcp_server = FastApiMCP(
            quest_app,
            auth_config=AuthConfig(dependencies=[Depends(verify_api_key)])
        )
        # Mount the MCP server's HTTP transport to our main app
        mcp_server.mount_http(app, mount_path="/mcp")
        _logger.info("Successfully built and mounted dynamic MCP server at /mcp")
        return app