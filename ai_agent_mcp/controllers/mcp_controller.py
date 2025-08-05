from odoo import http
from odoo.http import request
import json
import logging

_logger = logging.getLogger(__name__)


class MCPController(http.Controller):

    @http.route(['/<string:db_name>/mcp/resources', '/mcp/resources'],
                type='http', auth='user', methods=['GET'], csrf=False)
    def list_resources(self, db_name=None):
        """List available quest resources for MCP clients"""
        try:
            quests = request.env['ai.quest'].get_mcp_available_quests()
            resources = []

            for quest in quests:
                resources.append(quest.to_mcp_resource())

            response_data = {
                'resources': resources,
                'database': request.env.cr.dbname,
                'total_count': len(resources)
            }

            response = request.make_response(
                json.dumps(response_data, indent=2),
                headers=[
                    ('Content-Type', 'application/json'),
                    ('Access-Control-Allow-Origin', '*'),
                    ('Access-Control-Allow-Methods', 'GET, POST, OPTIONS'),
                    ('Access-Control-Allow-Headers', 'Content-Type, Authorization')
                ]
            )
            return response

        except Exception as e:
            _logger.error(f"Error listing MCP resources: {str(e)}")
            return request.make_response(
                json.dumps({'error': str(e)}),
                status=500,
                headers=[('Content-Type', 'application/json')]
            )

    @http.route(['/<string:db_name>/mcp/quest/<int:quest_id>/call',
                 '/mcp/quest/<int:quest_id>/call'],
                type='json', auth='user', methods=['POST'], csrf=False)
    def execute_quest(self, quest_id, db_name=None, **kwargs):
        """Execute quest via MCP"""
        try:
            quest = request.env['ai.quest'].browse(quest_id)

            if not quest.exists():
                return {'error': f'Quest {quest_id} not found'}

            # Extract parameters
            prompt = kwargs.get('prompt')
            record_id = kwargs.get('record_id')
            model = kwargs.get('model')

            if not prompt:
                return {'error': 'Parameter "prompt" is required'}

            # Execute quest via MCP
            result = quest.execute_via_mcp(
                prompt=prompt,
                record_id=record_id,
                model=model
            )

            return {
                'success': True,
                'result': result,
                'quest_id': quest_id,
                'quest_name': quest.name,
                'database': request.env.cr.dbname
            }

        except ValueError as e:
            _logger.warning(f"MCP Quest execution validation error: {str(e)}")
            return {'error': str(e)}
        except Exception as e:
            _logger.error(f"Error executing MCP quest {quest_id}: {str(e)}")
            return {'error': f'Internal error: {str(e)}'}

    @http.route(['/<string:db_name>/mcp/quest/<int:quest_id>',
                 '/mcp/quest/<int:quest_id>'],
                type='http', auth='user', methods=['GET'], csrf=False)
    def get_quest_details(self, quest_id, db_name=None):
        """Get detailed information about a specific quest"""
        try:
            quest = request.env['ai.quest'].browse(quest_id)

            if not quest.exists():
                return request.make_response(
                    json.dumps({'error': f'Quest {quest_id} not found'}),
                    status=404,
                    headers=[('Content-Type', 'application/json')]
                )

            if not quest.available_to_mcp:
                return request.make_response(
                    json.dumps({'error': 'Quest not available via MCP'}),
                    status=403,
                    headers=[('Content-Type', 'application/json')]
                )

            quest_data = {
                'id': quest.id,
                'name': quest.name,
                'description': quest.sub_description,
                'uri': quest.to_mcp_resource()['uri'],
                'parameters': {
                    'prompt': {
                        'type': 'string',
                        'required': True,
                        'description': 'The prompt to send to the quest'
                    },
                    'record_id': {
                        'type': 'integer',
                        'required': False,
                        'description': 'Optional: ID of record to process'
                    },
                    'model': {
                        'type': 'string',
                        'required': False,
                        'description': 'Optional: Model name for the record'
                    }
                },
                'database': request.env.cr.dbname
            }

            response = request.make_response(
                json.dumps(quest_data, indent=2),
                headers=[
                    ('Content-Type', 'application/json'),
                    ('Access-Control-Allow-Origin', '*')
                ]
            )
            return response

        except Exception as e:
            _logger.error(f"Error getting quest details for {quest_id}: {str(e)}")
            return request.make_response(
                json.dumps({'error': str(e)}),
                status=500,
                headers=[('Content-Type', 'application/json')]
            )

    # OPTIONS handler for CORS preflight requests
    @http.route(['/<string:db_name>/mcp/resources', '/mcp/resources',
                 '/<string:db_name>/mcp/quest/<int:quest_id>', '/mcp/quest/<int:quest_id>'],
                type='http', auth='none', methods=['OPTIONS'], csrf=False)
    def options_handler(self, **kwargs):
        """Handle CORS preflight requests"""
        print("------------------")
        return request.make_response(
            '',
            headers=[
                ('Access-Control-Allow-Origin', '*'),
                ('Access-Control-Allow-Methods', 'GET, POST, OPTIONS'),
                ('Access-Control-Allow-Headers', 'Content-Type, Authorization'),
                ('Access-Control-Max-Age', '86400')
            ]
        )