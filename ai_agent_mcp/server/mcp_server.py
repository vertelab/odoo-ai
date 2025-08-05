# from mcp.server.fastmcp import FastMCP
from fastmcp import FastMCP

import json
import logging
import os
import sys
import argparse
from contextlib import contextmanager

# Add Odoo path if not already in sys.path
odoo_path = os.environ.get('ODOO_PATH')
if odoo_path and odoo_path not in sys.path:
    sys.path.insert(0, odoo_path)

from odoo import api, registry, SUPERUSER_ID
from odoo.tools import config

_logger = logging.getLogger(__name__)


class MCPQuestServer:
    """MCP Server for Odoo AI Quests"""

    def __init__(self, database_name: str):
        self.database_name = database_name
        self.mcp = FastMCP(f"Odoo AI Quest Server - {database_name}")
        self._setup_routes()

    @contextmanager
    def get_env(self):
        """Get Odoo environment with proper cleanup"""
        db_registry = registry(self.database_name)
        cr = db_registry.cursor()
        try:
            env = api.Environment(cr, SUPERUSER_ID, {})
            yield env
        finally:
            cr.close()

    def _setup_routes(self):
        """Setup MCP routes"""

        @self.mcp.resource("mcp://odoo/{database}/quest/{quest_id}")
        def get_quest_resource(database: str, quest_id: str) -> str:
            """Get quest resource details"""
            if database != self.database_name:
                raise ValueError(f"Database mismatch: expected {self.database_name}, got {database}")

            with self.get_env() as env:
                try:
                    quest_id_int = int(quest_id)
                    quest = env['ai.quest'].browse(quest_id_int)

                    if not quest.exists():
                        raise ValueError(f"Quest {quest_id} not found")

                    if not quest.available_to_mcp:
                        raise ValueError("Quest not available via MCP")

                    quest_details = {
                        "id": quest.id,
                        "name": quest.name,
                        "description": quest.description,
                        "database": self.database_name,
                        "uri": f"mcp://odoo/{database}/quest/{quest_id}",
                        "tool_name": f"execute_quest_{quest.id}",
                        "parameters": {
                            "prompt": {
                                "type": "string",
                                "required": True,
                                "description": "The prompt to send to the quest"
                            },
                            "record_id": {
                                "type": "integer",
                                "required": False,
                                "description": "Optional: ID of record to process"
                            },
                            "model": {
                                "type": "string",
                                "required": False,
                                "description": "Optional: Model name for the record"
                            }
                        }
                    }

                    return json.dumps(quest_details, indent=2)

                except ValueError as e:
                    raise e
                except Exception as e:
                    _logger.error(f"Error getting quest resource {quest_id}: {e}")
                    raise ValueError(f"Internal error: {str(e)}")

        @self.mcp.tool()
        def execute_quest(quest_id: int, prompt: str, record_id: int = None, model: str = None) -> str:
            """Execute any AI Quest by ID"""
            with self.get_env() as env:
                try:
                    quest = env['ai.quest'].browse(quest_id)

                    if not quest.exists():
                        return json.dumps({"error": f"Quest {quest_id} not found"})

                    if not quest.available_to_mcp:
                        return json.dumps({"error": "Quest not available via MCP"})

                    # Execute quest
                    result = quest.execute_via_mcp(
                        prompt=prompt,
                        record_id=record_id,
                        model=model
                    )

                    return json.dumps({
                        "success": True,
                        "result": result,
                        "quest_id": quest_id,
                        "quest_name": quest.name,
                        "database": self.database_name
                    }, indent=2)

                except Exception as e:
                    _logger.error(f"Error executing quest {quest_id}: {e}")
                    return json.dumps({"error": str(e)})

        @self.mcp.tool()
        def list_available_quests() -> str:
            """List all available AI quests"""
            with self.get_env() as env:
                try:
                    quests = env['ai.quest'].search([('available_to_mcp', '=', True)])
                    quest_list = []

                    for quest in quests:
                        quest_info = quest.to_mcp_resource()
                        quest_info['tool_name'] = f"execute_quest_{quest.id}"
                        quest_list.append(quest_info)

                    return json.dumps({
                        "database": self.database_name,
                        "total_count": len(quest_list),
                        "quests": quest_list
                    }, indent=2)

                except Exception as e:
                    _logger.error(f"Error listing quests: {e}")
                    return json.dumps({"error": str(e)})

        @self.mcp.tool()
        def get_quest_info(quest_id: int) -> str:
            """Get detailed information about a specific quest"""
            with self.get_env() as env:
                try:
                    quest = env['ai.quest'].browse(quest_id)

                    if not quest.exists():
                        return json.dumps({"error": f"Quest {quest_id} not found"})

                    if not quest.available_to_mcp:
                        return json.dumps({"error": "Quest not available via MCP"})

                    quest_info = quest.to_mcp_resource()
                    quest_info.update({
                        "tool_definition": quest.get_mcp_tool_definition(),
                        "database": self.database_name
                    })

                    return json.dumps(quest_info, indent=2)

                except Exception as e:
                    _logger.error(f"Error getting quest info {quest_id}: {e}")
                    return json.dumps({"error": str(e)})

    def run(self, host: str = "localhost", port: int = 8000):
        """Run the MCP server"""
        _logger.info(f"Starting MCP server for database: {self.database_name}")
        _logger.info(f"Server will be available at: {host}:{port}")

        # Run FastMCP server
        # self.mcp.run(transport="sse")
        self.mcp.run(transport="sse")


def main():
    """Main entry point for running the MCP server"""
    parser = argparse.ArgumentParser(description='Run Odoo AI Quest MCP Server')
    parser.add_argument('--database', '-d', required=True, help='Odoo database name')
    parser.add_argument('--config', '-c', help='Odoo configuration file path')
    parser.add_argument('--host', default='localhost', help='Host to bind to (default: localhost)')
    parser.add_argument('--port', type=int, default=8000, help='Port to bind to (default: 8000)')
    parser.add_argument('--log-level', default='INFO', help='Log level (default: INFO)')

    args = parser.parse_args()

    # Setup logging
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    # Initialize Odoo config if config file provided
    if args.config:
        config.parse_config(['-c', args.config])

    # Verify database exists
    try:
        registry(args.database)
    except Exception as e:
        _logger.error(f"Cannot connect to database {args.database}: {e}")
        return 1

    # Create and run server
    server = MCPQuestServer(args.database)
    try:
        server.run()
    except KeyboardInterrupt:
        _logger.info("Server stopped by user")
    except Exception as e:
        _logger.error(f"Server error: {e}")
        return 1

    return 0


if __name__ == '__main__':
    sys.exit(main())