import logging
import re
import markdown
import html
from typing import List, Dict, Any
from fastapi import APIRouter, Body, HTTPException
from odoo.modules.registry import Registry
from odoo import api, SUPERUSER_ID
from odoo.http import request

_logger = logging.getLogger(__name__)

def get_available_quests(env: api.Environment) -> List[Dict[str, Any]]:
    """Get all ai.quest records that should be MCP tools."""
    quests = env['ai.quest'].sudo().search([('available_to_mcp', '=', True)], limit=100)
    if not quests:
        return []
    return [
        {
            'id': quest.id,
            'name': quest.name,
            'description': quest.description or f"Execute quest: {quest.name}",
        }
        for quest in quests
    ]

def create_dynamic_quest_router() -> APIRouter:
    """Create a FastAPI router with dynamically generated endpoints for each quest."""
    router = APIRouter()
    _logger.info("Attempting to create dynamic quest router...")

    try:
        # Use a temporary env just for startup route creation.
        # We use api.Environment.manage() to ensure the cursor is properly closed.
        temp_registry = Registry.new(request.env.cr.dbname)
        with temp_registry.cursor() as cr:
        # with api.Environment.manage() as env_manager:
            temp_env = api.Environment(cr, SUPERUSER_ID, {})
            quests = get_available_quests(temp_env)
            _logger.info(f"Found {len(quests)} quests to create as API endpoints.")

        for quest in quests:
            safe_name = re.sub(r'[^a-zA-Z0-9_]', '', quest['name'].lower().replace(' ', '_'))

            def create_endpoint_function(q_id, q_name):
                def quest_endpoint(prompt: str = Body(..., embed=True)) -> Dict[str, Any]:
                    _logger.info(f"Executing quest '{q_name}'.")
                    try:
                        temp_registry = Registry.new(request.env.cr.dbname)
                        with temp_registry.cursor() as cr:
                            odoo_env = api.Environment(cr, SUPERUSER_ID, {})
                            print("odoo env", odoo_env)
                            print("q_id", q_id)
                            print("prompt", prompt)
                            result = execute_quest_by_id(odoo_env, q_id, prompt)
                            return {"response": result}
                    except Exception as e:
                        _logger.error(f"Execution failed for quest '{q_name}': {e}", exc_info=True)
                        raise HTTPException(status_code=500, detail=str(e))
                return quest_endpoint

            endpoint_func = create_endpoint_function(quest['id'], quest['name'])

            router.add_api_route(
                f"/{safe_name}",
                endpoint_func,
                methods=["POST"],
                name=quest['name'],
                description=quest['description'],
                tags=["Dynamic Quests"],
                operation_id=safe_name,
            )
            _logger.info(f"✓ Registered API endpoint: /{safe_name}")

    except Exception as e:
        _logger.critical(f"FATAL: Could not create dynamic quest router. No tools will be available. Error: {e}", exc_info=True)

    return router

def execute_quest_by_id(env: api.Environment, quest_id: int, prompt: str, context: Dict[str, Any] = None) -> str:
    """Execute an ai.quest by ID and return formatted result."""
    try:
        # Get the quest directly by ID
        quest = env['ai.quest'].browse(quest_id)

        if not quest.exists():
            raise ValueError(f"Quest {quest_id} not found")

        if not quest.available_to_mcp:
            raise ValueError(f"Quest {quest_id} is not available for MCP")

        _logger.info(f"Executing quest '{quest.name}' (ID: {quest_id})")

        # Prepare quest parameters
        quest_params = {"prompt": prompt}
        if context:
            quest_params.update(context)

        # Execute the quest
        quest_response = quest.run(**quest_params)

        # Process the result
        # content = ""

        if quest_response:
            ai_message = quest._get_last_ai_message(
                quest_response.get('result', {}).get('messages', False)
            )
            if ai_message and hasattr(ai_message, 'content'):
                content = re.sub(
                    r'<think>.*?</think>', '', markdown.markdown(ai_message.content), flags=re.DOTALL
                )
                content = content.strip()
            else:
                content = "No AI message content found for this quest."
        else:
            content = "Quest completed successfully."

        print("content", content)

        return content

    except Exception as e:
        _logger.error(f"Failed to execute quest {quest_id}: {e}", exc_info=True)
        return f"Quest execution failed: {str(e)}"