import logging
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from odoo import api, SUPERUSER_ID
from odoo.http import request
from odoo.modules.registry import Registry

_logger = logging.getLogger(__name__)

# FastAPI security scheme
security = HTTPBearer()

async def verify_api_key(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """Dependency to verify the API key from the Authorization header using Odoo's API key system."""
    api_key = credentials.credentials
    try:
        # Create a temporary Odoo environment to check the API key
        # Dynamically get the database name from the current request context
        dbname = request.env.cr.dbname if request and request.env else None
        if not dbname:
            _logger.error("Could not determine database name for API key validation.")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Database context not found.")

        registry = Registry.new(dbname)
        with registry.cursor() as cr:
            env = api.Environment(cr, SUPERUSER_ID, {})
            
            # Use Odoo's built-in API key validation
            uid = env['res.users.apikeys']._check_credentials(scope='rpc', key=api_key)
            
            if not uid:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid API key",
                    headers={"WWW-Authenticate": "Bearer"}
                )
            _logger.info(f"API Key validated for user ID: {uid}")
            return uid # Return the user ID if validation is successful
    except Exception as e:
        _logger.error(f"Error during API key validation: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error during authentication",
        )