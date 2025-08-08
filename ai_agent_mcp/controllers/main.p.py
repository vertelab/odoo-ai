"""
FastAPI routers for AI Agent MCP integration.
This file defines the routers that will be automatically mounted by Odoo's fastapi module.
"""

import logging
import time
from typing import List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, status
from odoo import http
from odoo.http import request
from odoo.modules.registry import Registry
from odoo import api, SUPERUSER_ID
from ..security.auth import verify_api_key

_logger = logging.getLogger(__name__)

ai_agent_router = APIRouter(prefix="", tags=["AI Agent MCP"])

@ai_agent_router.get("/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "service": "AI Agent MCP Dynamic Tools",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }

@ai_agent_router.get("/quests", dependencies=[Depends(verify_api_key)])
async def list_quests() -> List[Dict[str, Any]]:
    """List all available AI quests for MCP."""
    try:
        dbname = request.env.cr.dbname if request and request.env else None
        if not dbname:
            _logger.error("Could not determine database name for listing quests.")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Database context not found.")
        registry = Registry(dbname)
        with registry.cursor() as cr:
            env = api.Environment(cr, SUPERUSER_ID, {})
            ai_quest_model = env['ai.quest'].sudo()
            domain = [('available_to_mcp', '=', True)]
            records = ai_quest_model.search(domain)
            quests = [{
                "id": record.id,
                "name": record.name,
                "description": record.description or f"Execute quest: {record.name}",
                "is_active": getattr(record, 'active', True),
            } for record in records]
            return quests
    except Exception as e:
        _logger.error(f"Error in quests endpoint: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


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
