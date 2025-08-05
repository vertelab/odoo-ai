from odoo.addons.fastapi.app import fastapi_app
from fastapi import APIRouter, HTTPException
import json
from odoo import api, registry, SUPERUSER_ID
from contextlib import contextmanager

from mcp.server.fastmcp import FastMCP

# Utility to get current database name from config/environment.
import odoo.tools.config

def get_db_name():
    # Adjust as needed for multi-db setups
    return odoo.tools.config['db_name']

@contextmanager
def get_env():
    db_name = get_db_name()
    reg = registry(db_name)
    cr = reg.cursor()
    try:
        yield api.Environment(cr, SUPERUSER_ID, {})
        cr.commit()
    except Exception:
        cr.rollback()
        raise
    finally:
        cr.close()

# Build MCP interface
mcp_router = FastMCP("Odoo MCP Quests")

# MCP resource endpoint: details for a specific quest
@mcp_router.resource("mcp://odoo/{database}/quest/{quest_id}")
def get_quest_resource(database: str, quest_id: str) -> str:
    if database != get_db_name():
        raise HTTPException(status_code=400, detail="Database mismatch.")
    with get_env() as env:
        quest = env['ai.quest'].browse(int(quest_id))
        if not quest.exists() or not quest.available_to_mcp:
            raise HTTPException(status_code=404, detail="Quest not available via MCP")
        quest_details = {
            "id": quest.id,
            "name": quest.name,
            "description": quest.description,
            "database": database,
            "uri": f"mcp://odoo/{database}/quest/{quest.id}",
            "tool_name": f"execute_quest_{quest.id}",
            "parameters": {
                "prompt": {"type": "string", "required": True, "description": "Prompt to send to the quest"},
                "record_id": {"type": "integer", "required": False},
                "model": {"type": "string", "required": False},
            }
        }
        return json.dumps(quest_details, indent=2)

# MCP tool: execute any quest
@mcp_router.tool()
def execute_quest(quest_id: int, prompt: str, record_id: int = None, model: str = None) -> str:
    with get_env() as env:
        quest = env['ai.quest'].browse(quest_id)
        if not quest.exists() or not quest.available_to_mcp:
            return json.dumps({"error": "Quest not available via MCP"})
        # You must implement .execute_via_mcp on ai.quest!
        result = quest.execute_via_mcp(prompt=prompt, record_id=record_id, model=model)
        return json.dumps({
            "success": True,
            "result": result,
            "quest_id": quest_id,
            "quest_name": quest.name,
            "database": get_db_name()
        }, indent=2)

# MCP tool: list all available quests
@mcp_router.tool()
def list_available_quests() -> str:
    with get_env() as env:
        quests = env['ai.quest'].search([('available_to_mcp', '=', True)])
        quest_list = []
        for quest in quests:
            quest_info = {
                "id": quest.id,
                "name": quest.name,
                "description": quest.description,
                "uri": f"mcp://odoo/{get_db_name()}/quest/{quest.id}"
            }
            quest_list.append(quest_info)
        return json.dumps({
            "database": get_db_name(),
            "quests": quest_list,
            "total_count": len(quest_list),
        }, indent=2)

# MCP tool: get detailed info for one quest
@mcp_router.tool()
def get_quest_info(quest_id: int) -> str:
    with get_env() as env:
        quest = env['ai.quest'].browse(quest_id)
        if not quest.exists() or not quest.available_to_mcp:
            return json.dumps({"error": "Quest not available via MCP"})
        quest_info = {
            "id": quest.id,
            "name": quest.name,
            "description": quest.description,
            "database": get_db_name(),
            # Add more fields as needed
        }
        return json.dumps(quest_info, indent=2)

# --- Mount FastMCP onto the Odoo FastAPI ASGI app --
fastapi_app.include_router(mcp_router, prefix="/mcp", tags=["MCP"])

# Optionally, add a health endpoint:
@fastapi_app.get("/mcp/health")
def mcp_health():
    return {"status": "ok"}

