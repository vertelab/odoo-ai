import logging
import re
import json
from typing import List, TYPE_CHECKING, Dict, Any, Optional
from fastapi import FastAPI, Depends, HTTPException
from fastapi_mcp import FastApiMCP
from fastapi_mcp.types import AuthConfig
import mcp.types as types

from odoo import api, fields, models, SUPERUSER_ID
from odoo.modules.registry import Registry
from odoo.http import request
from odoo.addons.ai_agent_mcp.controllers.main import ai_agent_router
from ..controllers.main import ai_agent_router
from ..services.mcp_tools import (
    create_dynamic_quest_router,
    get_all_model_names,
    get_model_definition,
    get_record_by_id,
    search_model_records,
)
from ..security.auth import verify_api_key

# Conditional import for APIRouter to avoid circular dependency during type checking
if TYPE_CHECKING:
    from fastapi import APIRouter

_logger = logging.getLogger(__name__)

class FastapiEndpoint(models.Model):
    _inherit = "fastapi.endpoint"

    app: str = fields.Selection(
        selection_add=[
            ("ai_agent_mcp", "AI Agent MCP")
        ],
        ondelete={"ai_agent_mcp": "cascade"}
    )

    # Class-level cache for MCP servers (keyed by endpoint ID)
    _mcp_servers_cache = {}
    _app_instances_cache = {}

    def _get_fastapi_routers(self) -> List["APIRouter"]:
        """Return the api routers to use for the instance."""
        if self.app == "ai_agent_mcp":
            return [ai_agent_router]
        return super()._get_fastapi_routers()

    def _get_app(self) -> FastAPI:
        """Override to add FastMCP integration for ai_agent_mcp app type."""
        if self.app == "ai_agent_mcp":
            # Return cached app instance or create new one
            cache_key = f"app_{self.id}"
            if cache_key not in self._app_instances_cache:
                self._app_instances_cache[cache_key] = self._get_mcp_enabled_app()
            return self._app_instances_cache[cache_key]
        return super()._get_app()

    def _get_stable_env(self):
        """Get a stable Odoo environment for database operations."""
        try:
            # First priority: use existing request environment
            if hasattr(request, 'env') and request.env:
                return request.env

            # Second priority: use self.env if it's valid
            if self.env and not self.env.cr.closed:
                return self.env

            # Last resort: create new environment with proper cursor management
            # Note: This should be used carefully as the caller needs to manage the cursor
            registry = Registry(self.env.cr.dbname)
            cr = registry.cursor()
            return api.Environment(cr, SUPERUSER_ID, {})

        except Exception as e:
            _logger.error(f"Error getting stable environment: {e}")
            # Return self.env as absolute fallback
            return self.env

    def _get_mcp_enabled_app(self) -> FastAPI:
        """Builds and returns the complete FastAPI app with dynamic MCP tools."""
        _logger.info("Building AI Agent MCP FastAPI app...")

        # Create main FastAPI app
        app = FastAPI(
            title="AI Agent Base API",
            version="1.0.0",
        )

        # Include the base router
        app.include_router(ai_agent_router)

        # Create quest app with proper error handling
        quest_app = FastAPI(
            title="AI Quest MCP Tools",
            description="Dynamically generated API endpoints for each AI Quest.",
        )

        # Add dynamic router with schema validation
        try:
            dynamic_router = create_dynamic_quest_router()
            quest_app.include_router(dynamic_router, prefix="/quests")
            _logger.info("Successfully added dynamic quest router")
        except Exception as e:
            _logger.error(f"Error creating dynamic quest router: {e}")
            # Create a minimal quest app if dynamic router fails
            quest_app = FastAPI(
                title="AI Quest MCP Tools (Minimal)",
                description="Minimal MCP server due to schema errors.",
            )

        # Create the MCP server instance and store it in class cache
        cache_key = f"mcp_{self.id}"
        try:
            mcp_server = FastApiMCP(
                quest_app,
                auth_config=AuthConfig(dependencies=[Depends(verify_api_key)])
            )
            self._mcp_servers_cache[cache_key] = mcp_server
            _logger.info("FastApiMCP server created successfully")
        except Exception as e:
            _logger.error(f"Error creating FastApiMCP server: {e}")
            raise

        # Register Odoo Resources with FastApiMCP's internal server
        self._register_odoo_resources(mcp_server)

        # Mount the MCP server's HTTP transport to our main app
        try:
            mcp_server.mount_http(app, mount_path="/mcp")
            _logger.info("Successfully mounted MCP server at /mcp")
        except Exception as e:
            _logger.error(f"Error mounting MCP server: {e}")
            raise

        return app

    def _register_odoo_resources(self, mcp_server: FastApiMCP):
        """Register Odoo resources with the MCP server."""
        if not mcp_server:
            _logger.error("MCP server not provided")
            return

        @mcp_server.server.list_resources()
        async def list_odoo_resources() -> List[types.Resource]:
            """Lists the available Odoo resource templates."""
            _logger.info("Listing Odoo resources...")

            try:
                resource_templates = [
                    {
                        "uri": "odoo://models",
                        "name": "Odoo Models",
                        "description": "List all available models in the Odoo system.",
                    },
                    {
                        "uri": "odoo://models/{model_name}",
                        "name": "Odoo Model Info",
                        "description": "Get detailed information and fields for a specific model.",
                    },
                    {
                        "uri": "odoo://records/{model_name}/{record_id}",
                        "name": "Odoo Record",
                        "description": "Get a specific record by its ID from a model.",
                    },
                    {
                        "uri": "odoo://records/{model_name}/search/{domain}",
                        "name": "Odoo Record Search",
                        "description": "Search for records in a model using a domain.",
                    },
                ]

                resources = [types.Resource(**rt) for rt in resource_templates]
                _logger.info(f"Returning {len(resources)} resources")
                return resources

            except Exception as e:
                _logger.error(f"Error listing resources: {e}", exc_info=True)
                return []

        @mcp_server.server.read_resource()
        async def read_odoo_resource(uri: str) -> str:
            """Reads data for a dynamic Odoo resource based on its URI."""
            _logger.info(f"Reading Odoo resource with URI: {uri}")

            try:
                data = None

                # Try to use existing environment first
                try:
                    if hasattr(request, 'env') and request.env and not request.env.cr.closed:
                        env = request.env
                        data = self._route_resource_uri(env, uri)
                    elif self.env and not self.env.cr.closed:
                        env = self.env
                        data = self._route_resource_uri(env, uri)
                except Exception as e:
                    _logger.warning(f"Could not use existing environment: {e}")

                # If existing env failed, create a new one with proper cursor management
                if data is None:
                    registry = Registry(self.env.cr.dbname)
                    with registry.cursor() as cr:
                        env = api.Environment(cr, SUPERUSER_ID, {})
                        data = self._route_resource_uri(env, uri)

                if data is None:
                    raise ValueError(f"Resource URI '{uri}' did not match any known pattern.")

                return json.dumps(data, indent=2)

            except ValueError as ve:
                _logger.warning(f"Value error while reading resource '{uri}': {ve}")
                raise types.ResourceError(str(ve))
            except Exception as e:
                _logger.error(f"Error reading resource '{uri}': {e}", exc_info=True)
                raise types.ResourceError(f"Internal server error: {str(e)}")

    def _route_resource_uri(self, env, uri: str):
        """Route URI to appropriate data handler."""

        uri_str = str(uri) if not isinstance(uri, str) else uri

        # Pattern 1: odoo://models
        if uri_str == "odoo://models":
            return get_all_model_names(env)

        # Pattern 2: odoo://models/{model_name}
        match = re.match(r"^odoo://models/([a-zA-Z0-9._-]+)$", uri_str)
        if match:
            model_name = match.group(1)
            return get_model_definition(env, model_name)

        # Pattern 3: odoo://records/{model_name}/{record_id}
        match = re.match(r"^odoo://records/([a-zA-Z0-9._-]+)/(\d+)$", uri_str)
        if match:
            model_name, record_id_str = match.groups()
            return get_record_by_id(env, model_name, int(record_id_str))

        # Pattern 4: odoo://records/{model_name}/search/{domain}
        match = re.match(r"^odoo://records/([a-zA-Z0-9._-]+)/search/(.*)$", uri_str)
        if match:
            model_name, domain_str = match.groups()
            return search_model_records(env, model_name, domain_str)

        return None

    def _cleanup_mcp_server(self):
        """Clean up MCP server resources."""
        cache_key = f"mcp_{self.id}"
        app_cache_key = f"app_{self.id}"

        try:
            if cache_key in self._mcp_servers_cache:
                del self._mcp_servers_cache[cache_key]
            if app_cache_key in self._app_instances_cache:
                del self._app_instances_cache[app_cache_key]
            _logger.info("MCP server cleaned up")
        except Exception as e:
            _logger.error(f"Error cleaning up MCP server: {e}")

    def unlink(self):
        """Override unlink to clean up resources."""
        self._cleanup_mcp_server()
        return super().unlink()

    @api.model_create_multi
    def create(self, vals_list):
        """Override create to ensure clean initialization."""
        records = super().create(vals_list)
        # No need for manual initialization with class-level cache
        return records