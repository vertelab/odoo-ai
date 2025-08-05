import logging
import threading
from odoo import api, SUPERUSER_ID
from odoo.modules.registry import Registry

_logger = logging.getLogger(__name__)

# Store server instances per database
_mcp_servers = {}

# Store the original method
_original_setup_models = Registry.setup_models


def mcp_enhanced_setup_models(self, cr):
    """Enhanced setup_models that auto-starts MCP server"""
    # Call original method first
    result = _original_setup_models(self, cr)

    # Auto-start MCP server for this database
    db_name = self.db_name

    # Only start once per database
    if db_name in _mcp_servers:
        return result

    try:
        # Use the same cr that was passed to setup_models
        env = api.Environment(cr, SUPERUSER_ID, {})

        # Check if ai_agent_mcp module is installed
        mcp_module = env['ir.module.module'].search([
            ('name', '=', 'ai_agent_mcp'),
            ('state', '=', 'installed')
        ]).exists()

        if not mcp_module:
            return result

        # Check if we have any MCP-enabled quests
        mcp_quest_count = env['ai.quest'].search_count([('available_to_mcp', '=', True)])

        if mcp_quest_count == 0:
            _logger.info(f"No MCP-enabled quests found for {db_name}, skipping MCP server start")
            return result

        _logger.info(f"Starting MCP server for database: {db_name} ({mcp_quest_count} quests available)")

        def start_mcp_server():
            try:
                # Import here to avoid circular imports
                # Try different import approaches
                try:
                    from odoo.addons.ai_agent_mcp.server.mcp_server import MCPQuestServer
                except ImportError:
                    # Fallback: direct module import
                    import importlib
                    module = importlib.import_module('odoo.addons.ai_agent_mcp.server.mcp_server')
                    MCPQuestServer = module.MCPQuestServer

                # Create and start server
                server = MCPQuestServer(db_name)

                # Store server instance
                _mcp_servers[db_name] = server

                # Default port: 8000 + hash of db_name to avoid conflicts
                port = 8000 + (hash(db_name) % 1000)

                _logger.info(f"MCP server starting for {db_name} on localhost:{port}")
                server.run()

            except Exception as e:
                _logger.error(f"MCP server error for {db_name}: {e}")
                # Clean up on error
                if db_name in _mcp_servers:
                    del _mcp_servers[db_name]

        # Start server in background thread with small delay
        def delayed_start():
            import time
            time.sleep(1)  # Wait for registry to be fully ready
            start_mcp_server()

        thread = threading.Thread(target=delayed_start, daemon=True, name=f'MCP-{db_name}')
        thread.start()

    except Exception as e:
        _logger.error(f"Error starting MCP server for {db_name}: {e}")

    return result


# Apply the monkey patch - just like your Registry.init approach
Registry.setup_models = mcp_enhanced_setup_models

_logger.info("MCP Registry hook installed - MCP servers will auto-start for databases with MCP-enabled quests")