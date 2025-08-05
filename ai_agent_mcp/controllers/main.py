import werkzeug
import json
import logging
from typing import Annotated, Optional, List

from odoo.addons.fastapi.dependencies import odoo_env
from fastapi import APIRouter, Depends, HTTPException, status, Query
from fastapi.security import APIKeyHeader
from fastapi_mcp import FastApiMCP
from fastmcp import FastMCP


from odoo.http import request
from odoo import http
from odoo.exceptions import AccessDenied
from odoo.api import Environment
from odoo.addons.ai_agent_mcp.models.ai_agent_fastapi_endpoint import ai_agent_router
from odoo.addons.ai_agent_mcp.models.ai_agent_fastapi_schemas import (
    AIQuestBase, AIQuestListRequest, AIQuestListResponse, AIQuestItem,
    AIToolBase, AIToolListResponse, AIToolItem
)

_logger = logging.getLogger(__name__)



def authenticate_api_key(api_key: str, env: Environment) -> int:
    """Authenticate API key and return user ID."""
    uid = env['res.users.apikeys']._check_credentials(scope='rpc', key=api_key)
    if not uid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
            headers={"WWW-Authenticate": "Bearer"}
        )
    return uid


@ai_agent_router.get("/tools", response_model=AIQuestListResponse)
def list_ai_quest(
        env: Annotated[Environment, Depends(odoo_env)],
        api_key: Annotated[str, Depends(
            APIKeyHeader(
                name="api-key",
                description="API key for authentication. Get yours from your user account.",
            )
        )],
        limit: Annotated[int, Query(description="Maximum number of AI quests to return", ge=1, le=100)] = 10,
        offset: Annotated[int, Query(description="Number of AI quests to skip", ge=0)] = 0,
) -> AIQuestListResponse:
    """
    List AI quests that are enabled for MCP.

    Args:
        env: Odoo environment
        api_key: API key for authentication
        limit: Maximum number of AI quests to return (1-100)
        offset: Number of AI quests to skip for pagination

    Returns:
        AIQuestListResponse with list of AI quests
    """
    # Authenticate user
    uid = authenticate_api_key(api_key, env)
    _logger.info(f"Listing AI quests for user {uid} (limit: {limit}, offset: {offset})")

    try:
        # Search for AI quests that are enabled for MCP
        ai_quest_model = env['ai.quest']
        domain = [('available_to_mcp', '=', True)]

        # Get total count for pagination info
        total_count = ai_quest_model.search_count(domain)

        # Get the records with pagination
        records = ai_quest_model.search(domain, limit=limit, offset=offset)

        if not records:
            return AIQuestListResponse(
                success=True,
                message="No AI quests found",
                quests=[],
                total_count=total_count,
                limit=limit,
                offset=offset
            )

        # Convert records to response format
        quests = []
        for record in records:
            quests.append(AIQuestItem(
                id=record.id,
                name=record.name,
                description=getattr(record, 'description', None),
                is_active=getattr(record, 'active', True),
                created_date=record.create_date.isoformat() if record.create_date else None,
                modified_date=record.write_date.isoformat() if record.write_date else None,
            ))

        return AIQuestListResponse(
            success=True,
            message=f"Found {len(quests)} AI quests",
            quests=quests,
            total_count=total_count,
            limit=limit,
            offset=offset
        )

    except HTTPException:
        raise
    except Exception as e:
        _logger.exception(f"Error retrieving AI quests for user {uid}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve AI quests: {str(e)}"
        )


@ai_agent_router.get("/call", response_model=AIQuestItem)
def get_ai_quest_by_id(
        quest_id: int,
        env: Annotated[Environment, Depends(odoo_env)],
        api_key: Annotated[str, Depends(
            APIKeyHeader(
                name="api-key",
                description="API key for authentication. Get yours from your user account.",
            )
        )],
) -> AIQuestItem:
    """
    Get a specific AI quest by ID.

    Args:
        quest_id: ID of the AI quest to retrieve
        env: Odoo environment
        api_key: API key for authentication

    Returns:
        AIQuestItem with quest details
    """
    # Authenticate user
    uid = authenticate_api_key(api_key, env)
    _logger.info(f"Getting AI quest {quest_id} for user {uid}")

    try:
        ai_quest_model = env['ai.quest']
        record = ai_quest_model.search([('id', '=', quest_id), ('available_to_mcp', '=', True)], limit=1)

        if not record:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"AI quest with ID '{quest_id}' not found or not enabled for MCP"
            )

        return AIQuestItem(
            id=record.id,
            name=record.name,
            description=getattr(record, 'description', None),
            is_active=getattr(record, 'active', True),
            created_date=record.create_date.isoformat() if record.create_date else None,
            modified_date=record.write_date.isoformat() if record.write_date else None,
        )

    except HTTPException:
        raise
    except Exception as e:
        _logger.exception(f"Error retrieving AI quest {quest_id}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve AI quest: {str(e)}"
        )


# mcp = FastMCP.run()

# mcp = FastApiMCP(
#     ai_agent_router,
#     name="MCP Server for AI Quest Tools",
#     description="Access AI Quest Tools via MCP",
# )
#
# mcp.mount_http()