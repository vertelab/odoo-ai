import logging
import re
import markdown
import html
import json
from typing import List, Dict, Any, Type
from fastapi import APIRouter, Body, HTTPException, Depends
from odoo.modules.registry import Registry
from odoo import api, SUPERUSER_ID
from odoo.http import request
from odoo.exceptions import UserError
from pydantic import BaseModel, create_model

_logger = logging.getLogger(__name__)


def safe_json_serializer(json_str):
    """Safely parse JSON schema with validation."""
    if not json_str:
        return {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "User input"
                }
            },
            "required": ["prompt"]
        }

    try:
        # Use json.loads instead of eval for security
        schema = json.loads(json_str) if isinstance(json_str, str) else json_str

        # Validate basic schema structure
        if not isinstance(schema, dict):
            _logger.warning(f"Invalid schema format: {type(schema)}")
            return {
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "User input"
                    }
                },
                "required": ["prompt"]
            }

        # Ensure required schema properties exist
        validated_schema = {
            "type": schema.get("type", "object"),
            "properties": schema.get("properties", {}),
        }

        # Add required fields if they exist
        if "required" in schema:
            validated_schema["required"] = schema["required"]

        # Ensure properties is not empty
        if not validated_schema["properties"]:
            validated_schema["properties"] = {
                "prompt": {
                    "type": "string",
                    "description": "User input"
                }
            }
            validated_schema["required"] = ["prompt"]

        return validated_schema

    except (json.JSONDecodeError, TypeError, ValueError) as e:
        _logger.error(f"JSON schema parsing error: {e}")
        return {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "User input"
                }
            },
            "required": ["prompt"]
        }


# Keep old function name for backward compatibility
def json_serializer(json_str):
    """Legacy function - now uses safe parsing."""
    return safe_json_serializer(json_str)


def get_available_quests(env: api.Environment) -> List[Dict[str, Any]]:
    """Safely get all ai.quest records that should be MCP tools."""
    try:
        # Ensure we only get quests with a tool_name, as it's required.
        quests = env['ai.quest'].sudo().search([
            ('available_to_mcp', '=', True),
            ('tool_name', '!=', False)
        ], limit=100)

        if not quests:
            _logger.info("No MCP-enabled quests found")
            return []

        quest_list = []
        for quest in quests:
            try:
                # Validate tool_name
                tool_name = quest.tool_name
                if not tool_name or not isinstance(tool_name, str):
                    _logger.warning(f"Quest {quest.id} has invalid tool_name: {tool_name}")
                    continue

                # Clean tool name (remove special characters that could cause issues)
                clean_tool_name = re.sub(r'[^a-zA-Z0-9_-]', '_', tool_name)

                quest_data = {
                    'id': quest.id,
                    'name': clean_tool_name,
                    'title': quest.name or f"Quest {quest.id}",
                    'description': quest.sub_description or f"Execute quest: {quest.name}",
                    'inputSchema': safe_json_serializer(quest.input_schema),
                }
                quest_list.append(quest_data)
                _logger.debug(f"Added quest: {clean_tool_name}")

            except Exception as e:
                _logger.error(f"Error processing quest {quest.id}: {e}")
                continue

        _logger.info(f"Successfully loaded {len(quest_list)} quests for MCP")
        return quest_list

    except Exception as e:
        _logger.error(f"Error getting available quests: {e}")
        return []


def create_pydantic_model(model_name: str, schema: Dict[str, Any]) -> Type[BaseModel]:
    """Dynamically create a Pydantic model from a JSON schema."""
    if not schema or 'properties' not in schema:
        # Return a model for the default 'prompt' argument if schema is empty
        return create_model(model_name, prompt=(str, ...))

    try:
        # Map JSON schema types to Python types
        type_mapping = {
            'string': str,
            'integer': int,
            'number': float,
            'boolean': bool,
            'array': list,
            'object': dict,
        }

        fields = {}
        properties = schema.get('properties', {})
        required_fields = schema.get('required', [])

        for prop_name, prop_schema in properties.items():
            # Clean property name
            clean_prop_name = re.sub(r'[^a-zA-Z0-9_]', '_', prop_name)

            # Get property type
            prop_type_str = prop_schema.get('type', 'string')
            prop_type = type_mapping.get(prop_type_str, str)

            # Determine if field is required
            default_value = ... if prop_name in required_fields else None

            fields[clean_prop_name] = (prop_type, default_value)

        # Ensure at least one field exists
        if not fields:
            fields['prompt'] = (str, ...)

        return create_model(model_name, **fields)

    except Exception as e:
        _logger.error(f"Error creating Pydantic model for {model_name}: {e}")
        # Fallback to simple prompt model
        return create_model(model_name, prompt=(str, ...))


def create_dynamic_quest_router() -> APIRouter:
    """Create a FastAPI router with dynamically generated endpoints for each quest."""
    router = APIRouter()
    _logger.info("Attempting to create dynamic quest router...")

    try:
        temp_registry = Registry.new(request.env.cr.dbname)
        with temp_registry.cursor() as cr:
            temp_env = api.Environment(cr, SUPERUSER_ID, {})
            quests = get_available_quests(temp_env)
            _logger.info(f"Found {len(quests)} quests to create as API endpoints.")

        for quest in quests:
            try:
                tool_name = quest['name']

                # Validate tool name
                if not tool_name or not isinstance(tool_name, str):
                    _logger.warning(f"Skipping quest {quest['id']} due to invalid tool name")
                    continue

                # Create a Pydantic model for the quest's input schema
                model_name = f"Quest{quest['id']}Args"
                ArgsModel = create_pydantic_model(model_name, quest['inputSchema'])

                def create_endpoint_function(q_id, q_title, q_tool_name):
                    def quest_endpoint(args: ArgsModel = Body(..., embed=True)) -> Dict[str, Any]:
                        _logger.info(f"Executing quest '{q_title}' via tool '{q_tool_name}'.")
                        try:
                            temp_registry = Registry.new(request.env.cr.dbname)
                            with temp_registry.cursor() as cr:
                                odoo_env = api.Environment(cr, SUPERUSER_ID, {})
                                # Pass arguments as a dictionary
                                result = execute_quest_by_id(odoo_env, q_id, args.model_dump())
                                return {"response": result}
                                # return result
                        except Exception as e:
                            _logger.error(f"Execution failed for quest '{q_title}': {e}", exc_info=True)
                            raise HTTPException(status_code=500, detail=str(e))

                    return quest_endpoint

                endpoint_func = create_endpoint_function(quest['id'], quest['title'], tool_name)

                router.add_api_route(
                    f"/{tool_name}",
                    endpoint_func,
                    methods=["POST"],
                    name=quest['title'],
                    description=quest['description'],
                    tags=["Dynamic Quests"],
                    operation_id=tool_name,
                )
                _logger.info(f"✓ Registered API endpoint: /{tool_name}")

            except Exception as e:
                _logger.error(f"Error creating endpoint for quest {quest.get('id', 'unknown')}: {e}")
                continue

    except Exception as e:
        _logger.error(f"Critical error creating dynamic quest router: {e}", exc_info=True)

    return router


def execute_quest_by_id(env: api.Environment, quest_id: int, arguments: Dict[str, Any],
                        context: Dict[str, Any] = None) -> str:
    """Execute an ai.quest by ID and return formatted result."""
    try:
        quest = env['ai.quest'].browse(quest_id)

        if not quest.exists():
            raise ValueError(f"Quest {quest_id} not found")

        if not quest.available_to_mcp:
            raise ValueError(f"Quest {quest_id} is not available for MCP")

        _logger.info(f"Executing quest '{quest.name}' (ID: {quest_id}) with args: {arguments}")

        # Prepare quest parameters
        quest_params = arguments
        if context:
            quest_params.update(context)

        quest_response = quest.run(**quest_params)

        if quest_response:
            ai_message = quest._get_last_ai_message(
                quest_response.get('result', {}).get('messages', False)
            )
            if ai_message and hasattr(ai_message, 'content'):
                content = re.sub(
                    # r'<think>.*?</think>', '', markdown.markdown(ai_message.content), flags=re.DOTALL
                    r'<think>.*?</think>', '', ai_message.content, flags=re.DOTALL
                )
                content = content.strip()
            else:
                content = "No AI message content found for this quest."
        else:
            content = "Quest completed successfully."
        return content

    except Exception as e:
        _logger.error(f"Failed to execute quest {quest_id}: {e}", exc_info=True)
        return f"Quest execution failed: {str(e)}"


# --- Odoo Resource Helper Functions ---

def get_all_model_names(env: api.Environment) -> List[Dict[str, str]]:
    """Fetches all Odoo models."""
    _logger.info("Fetching all Odoo models.")
    models = env['ir.model'].sudo().search([])
    return [{'model': m.model, 'name': m.name} for m in models]


def get_model_definition(env: api.Environment, model_name: str) -> Dict[str, Any]:
    """Gets the definition of a specific Odoo model."""
    _logger.info(f"Fetching definition for model: {model_name}")
    if model_name not in env:
        raise ValueError(f"Model '{model_name}' not found in Odoo environment.")

    model = env[model_name]
    fields = model.fields_get()

    return {
        'model': model._name,
        'name': model._description,
        'fields': {fname: {
            'type': finfo['type'],
            'string': finfo['string'],
            'required': finfo.get('required', False),
            'help': finfo.get('help', ''),
            'relation': finfo.get('relation'),
        } for fname, finfo in fields.items()}
    }


def get_record_by_id(env: api.Environment, model_name: str, record_id: int) -> Dict[str, Any]:
    """Gets a single record from a model by its ID."""
    _logger.info(f"Fetching record from model {model_name} with ID {record_id}")
    if model_name not in env:
        raise ValueError(f"Model '{model_name}' not found in Odoo environment.")

    record = env[model_name].sudo().search_read([('id', '=', record_id)], limit=1)
    if not record:
        raise ValueError(f"Record with ID {record_id} not found in model {model_name}.")
    return record[0]


def search_model_records(env: api.Environment, model_name: str, domain_str: str) -> List[Dict[str, Any]]:
    """Searches for records in a model using a JSON domain."""
    _logger.info(f"Searching model {model_name} with domain: {domain_str}")
    if model_name not in env:
        raise ValueError(f"Model '{model_name}' not found in Odoo environment.")

    try:
        # WARNING: Loading a domain from a string is powerful but can be risky
        # if the source is not trusted.
        domain = json.loads(domain_str)
    except json.JSONDecodeError:
        raise ValueError("Invalid domain format. Must be a valid JSON string.")

    records = env[model_name].sudo().search_read(domain, limit=20)  # Hardcoded limit for safety
    return records