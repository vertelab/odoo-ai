"""
FastAPI routers for AI Agent MCP integration.
This file defines the routers that will be automatically mounted by Odoo's fastapi module.
"""

import logging
import time
import re
import json
from typing import List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from odoo import http
from odoo.http import request
from odoo.modules.registry import Registry
from odoo import api, SUPERUSER_ID
from ..security.auth import verify_api_key
from ..services.mcp_tools import (
    get_available_quests,
    execute_quest_by_id,
    get_all_model_names,
    get_model_definition,
    get_record_by_id,
    search_model_records,
)

_logger = logging.getLogger(__name__)

ai_agent_router = APIRouter(prefix="", tags=["AI Agent MCP"])

# @ai_agent_router.get("/")
# async def root_status():
#     """Root endpoint for API status check."""
#     return {"status": "ok", "message": "AI Agent MCP API is running."}
#
# @ai_agent_router.get("/health")
# async def health_check():
#     """Health check endpoint."""
#     return {
#         "status": "healthy",
#         "service": "AI Agent MCP Dynamic Tools",
#         "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
#     }
#
# @ai_agent_router.get("/tools/list", dependencies=[Depends(verify_api_key)])
# async def tools_list() -> Dict[str, Any]:
#     """List all available tools for MCP."""
#     try:
#         dbname = request.env.cr.dbname if request and request.env else None
#         if not dbname:
#             _logger.error("Could not determine database name for listing tools.")
#             raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Database context not found.")
#
#         registry = Registry(dbname)
#         with registry.cursor() as cr:
#             env = api.Environment(cr, SUPERUSER_ID, {})
#             quests = get_available_quests(env)
#
#             tools = [
#                 {
#                     "name": quest['tool_name'],
#                     "title": quest['title'],
#                     "description": quest['description'],
#                     "inputSchema": quest['input_schema'],
#                 }
#                 for quest in quests
#             ]
#
#             return {"tools": tools}
#
#     except Exception as e:
#         _logger.error(f"Error in tools/list endpoint: {e}", exc_info=True)
#         raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))
#
#
# # --- Simplified Tool Calling Endpoint ---
#
# class SimpleToolCallRequest(BaseModel):
#     name: str
#     arguments: Dict[str, Any]
#
# @ai_agent_router.post("/tools/call", dependencies=[Depends(verify_api_key)])
# async def tools_call(call_request: SimpleToolCallRequest) -> Dict[str, Any]:
#     """Execute a tool by name with the given arguments."""
#     tool_name = call_request.name
#     arguments = call_request.arguments
#
#     try:
#         dbname = request.env.cr.dbname
#         if not dbname:
#             raise HTTPException(status_code=500, detail="Database context not found.")
#
#         registry = Registry(dbname)
#         with registry.cursor() as cr:
#             env = api.Environment(cr, SUPERUSER_ID, {})
#
#             # Efficiently find the quest by the stored tool_name.
#             quest_model = env['ai.quest'].sudo()
#             quest = quest_model.search([('tool_name', '=', tool_name)], limit=1)
#
#             if not quest:
#                 raise HTTPException(status_code=404, detail=f"Tool '{tool_name}' not found.")
#
#             # Execute the quest
#             result_text = execute_quest_by_id(env, quest.id, arguments)
#
#             # Return a simple response
#             return {"response": result_text}
#
#     except Exception as e:
#         _logger.error(f"Error in tools/call endpoint for tool '{tool_name}': {e}", exc_info=True)
#         raise HTTPException(status_code=500, detail=str(e))
#
# # --- END OF SIMPLIFIED ENDPOINT ---
#
#
# # --- Odoo Dynamic Resources ---
#
# @ai_agent_router.get("/resources/list", dependencies=[Depends(verify_api_key)])
# async def resources_list() -> Dict[str, Any]:
#     """Lists the available Odoo resource templates."""
#     resource_templates = [
#         {
#             "uri": "odoo://models",
#             "name": "Odoo Models",
#             "description": "List all available models in the Odoo system.",
#         },
#         {
#             "uri": "odoo://models/{model_name}",
#             "name": "Odoo Model Info",
#             "description": "Get detailed information and fields for a specific model.",
#         },
#         {
#             "uri": "odoo://records/{model_name}/{record_id}",
#             "name": "Odoo Record",
#             "description": "Get a specific record by its ID from a model.",
#         },
#         {
#             "uri": "odoo://records/{model_name}/search/{domain}",
#             "name": "Odoo Record Search",
#             "description": "Search for records in a model using a domain.",
#         },
#     ]
#     return {"resources": resource_templates}
#
# # --- END OF Odoo Dynamic Resources ---
#
# class ResourceReadRequest(BaseModel):
#     uri: str
#
# @ai_agent_router.post("/resources/read", dependencies=[Depends(verify_api_key)])
# async def resources_read(read_request: ResourceReadRequest) -> Dict[str, Any]:
#     """Reads data for a dynamic Odoo resource based on its URI."""
#     uri = read_request.uri
#     _logger.info(f"Reading resource with URI: {uri}")
#
#     try:
#         dbname = request.env.cr.dbname
#         if not dbname:
#             raise HTTPException(status_code=500, detail="Database context not found.")
#
#         registry = Registry(dbname)
#         with registry.cursor() as cr:
#             env = api.Environment(cr, SUPERUSER_ID, {})
#
#             # --- URI ROUTER ---
#             data = None
#             content_type = "application/json"
#
#             # Pattern 1: odoo://models
#             if uri == "odoo://models":
#                 data = get_all_model_names(env)
#
#             # Pattern 2: odoo://models/{model_name}
#             match = re.match(r"^odoo://models/([a-zA-Z0-9._-]+)$", uri)
#             if not data and match:
#                 model_name = match.group(1)
#                 data = get_model_definition(env, model_name)
#
#             # Pattern 3: odoo://records/{model_name}/{record_id}
#             match = re.match(r"^odoo://records/([a-zA-Z0-9._-]+)/(\d+)$", uri)
#             if not data and match:
#                 model_name, record_id_str = match.groups()
#                 data = get_record_by_id(env, model_name, int(record_id_str))
#
#             # Pattern 4: odoo://records/{model_name}/search/{domain}
#             match = re.match(r"^odoo://records/([a-zA-Z0-9._-]+)/search/(.*)$", uri)
#             if not data and match:
#                 model_name, domain_str = match.groups()
#                 data = search_model_records(env, model_name, domain_str)
#
#             if data is None:
#                 raise HTTPException(status_code=404, detail=f"Resource URI '{uri}' did not match any known pattern.")
#
#             # The 'data' object is the raw list or dict from our service function.
#             # We return it directly in the 'structuredContent' field for a clean response.
#             return {
#                 "content": [{
#                     "type": "text",
#                     "text": f"Successfully read from resource '{uri}'.",
#                 }],
#                 "structuredContent": data
#             }
#
#     except ValueError as ve:
#         _logger.warning(f"Value error while reading resource '{uri}': {ve}")
#         raise HTTPException(status_code=400, detail=str(ve))
#     except Exception as e:
#         _logger.error(f"Error reading resource '{uri}': {e}", exc_info=True)
#         raise HTTPException(status_code=500, detail=str(e))

# --- END OF Odoo Dynamic Resources ---


# @ai_agent_router.get("/quests", dependencies=[Depends(verify_api_key)])
# async def list_quests() -> List[Dict[str, Any]]:
#     """List all available AI quests for MCP."""
#     try:
#         dbname = request.env.cr.dbname if request and request.env else None
#         if not dbname:
#             _logger.error("Could not determine database name for listing quests.")
#             raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Database context not found.")
#         registry = Registry(dbname)
#         with registry.cursor() as cr:
#             env = api.Environment(cr, SUPERUSER_ID, {})
#             ai_quest_model = env['ai.quest'].sudo()
#             domain = [('available_to_mcp', '=', True)]
#             records = ai_quest_model.search(domain)
#             quests = [{
#                 "id": record.id,
#                 "name": record.name,
#                 "description": record.description or f"Execute quest: {record.name}",
#                 "is_active": getattr(record, 'active', True),
#             } for record in records]
#             return quests
#     except Exception as e:
#         _logger.error(f"Error in quests endpoint: {e}", exc_info=True)
#         raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


class MyModuleController(http.Controller):

    @http.route('/ai_agent/download_code/<int:record_id>', type='http', auth='user')
    def download_code(self, record_id, **kwargs):
        record = request.env['ai.quest'].browse(record_id)
        zip_bytes = record.generate_simple_module()  # Din metod som skapar zip
        filename = 'my_module.zip'
        headers = [
            ('Content-Type', 'application/zip'),
            ('Content-Disposition', f'attachment; filename="{filename}"'),
        ]
        return request.make_response(zip_bytes, headers)
