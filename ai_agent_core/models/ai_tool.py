# -*- coding: utf-8 -*-
"""
Custom Tools — user-defined tools via ai.tool (TOOL-002).

ai.tool lets users define custom tools with Python code.
Tools are evaluated in a sandboxed context for safety.
"""

import json
import logging

from odoo import models, fields, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

RISK_LEVELS = [
    ('safe', 'Safe'),
    ('read_only', 'Read Only'),
    ('write', 'Write'),
    ('destructive', 'Destructive'),
    ('execute', 'Execute Code'),
]


class AITool(models.Model):
    """A user-defined tool that agents can call."""
    _name = 'ai.tool'
    _description = 'AI Tool'
    _order = 'name asc'

    name = fields.Char('Tool Name', required=True)
    active = fields.Boolean(default=True)
    description = fields.Text(
        'Description', required=True,
        help='What this tool does. Shown to the LLM for tool selection.',
    )
    code = fields.Text(
        'Code', required=True,
        help=(
            'Python code for this tool. Must define an async function '
            'named "execute" that takes keyword arguments and returns a string. '
            'Access Odoo ORM via "env" variable. '
            'Example:\n'
            'async def execute(domain=None, limit=10):\n'
            '    records = env["res.partner"].search(domain or [], limit=limit)\n'
            '    return "\\n".join(r.name for r in records)'
        ),
    )
    parameters = fields.Text(
        'Parameters (JSON Schema)',
        default='{"type": "object", "properties": {}, "required": []}',
        help='JSON Schema for tool parameters. Defines what the LLM can pass.',
    )
    risk_level = fields.Selection(RISK_LEVELS, default='read_only', required=True)

    # Relations
    agent_ids = fields.Many2many(
        'ai.agent', 'ai_agent_tool_custom_rel',
        'tool_id', 'agent_id', string='Used by Agents',
    )
    quest_ids = fields.Many2many(
        'ai.quest', 'ai_quest_tool_custom_rel',
        'tool_id', 'quest_id', string='Used by Quests',
    )

    # Stats
    call_count = fields.Integer('Times Called', default=0)
    last_called = fields.Datetime('Last Called')
    error_count = fields.Integer('Errors', default=0)

    # Sandbox
    sandbox_enabled = fields.Boolean('Sandbox', default=True,
                                      help='Restrict code to safe operations')

    _sql_constraints = [
        ('name_unique', 'unique(name)', 'Tool name must be unique!'),
    ]

    @api.constrains('parameters')
    def _check_parameters_json(self):
        for rec in self:
            if rec.parameters:
                try:
                    params = json.loads(rec.parameters)
                    if not isinstance(params, dict):
                        raise UserError(_('Parameters must be a JSON object'))
                    if 'type' not in params:
                        raise UserError(_('Parameters must include "type" field'))
                except json.JSONDecodeError:
                    raise UserError(_('Parameters must be valid JSON'))

    def action_test(self):
        """Test the tool by executing it with empty/default parameters."""
        self.ensure_one()
        try:
            params = json.loads(self.parameters)
            test_args = {}
            for prop_name, prop_def in params.get('properties', {}).items():
                prop_type = prop_def.get('type', 'string')
                if prop_type == 'string':
                    test_args[prop_name] = ''
                elif prop_type == 'integer':
                    test_args[prop_name] = 0
                elif prop_type == 'boolean':
                    test_args[prop_name] = False
                elif prop_type == 'array':
                    test_args[prop_name] = []

            result = self._execute_tool(test_args)
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Tool Test OK'),
                    'message': str(result)[:200],
                    'type': 'success',
                }
            }
        except Exception as e:
            self.error_count += 1
            raise UserError(_('Tool test failed: %s') % str(e))

    def _execute_tool(self, kwargs: dict) -> str:
        """Execute the tool code with given parameters."""
        self.ensure_one()

        # Sandboxed environment
        allowed_builtins = {
            'True': True, 'False': False, 'None': None,
            'str': str, 'int': int, 'float': float, 'bool': bool,
            'list': list, 'dict': dict, 'len': len,
            'range': range, 'enumerate': enumerate, 'zip': zip,
            'print': lambda *a, **kw: None,  # no-op in sandbox
        }

        safe_globals = {
            '__builtins__': allowed_builtins,
            'env': self.env,
            'json': __import__('json'),
            'datetime': __import__('datetime'),
        }

        try:
            # Compile and execute the tool code
            compiled = compile(self.code, f'<tool:{self.name}>', 'exec')
            exec(compiled, safe_globals)

            # Get the execute function
            execute_func = safe_globals.get('execute')
            if not execute_func:
                raise UserError(_('Tool must define an "execute" function'))

            if not callable(execute_func):
                raise UserError(_('"execute" must be a function'))

            # Call synchronously (Odoo is sync)
            import asyncio
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    # We're in an async context — use run_coroutine_threadsafe
                    result = asyncio.run_coroutine_threadsafe(
                        execute_func(**kwargs), loop
                    ).result(timeout=30)
                else:
                    result = loop.run_until_complete(execute_func(**kwargs))
            except RuntimeError:
                # No running loop — create one
                result = asyncio.run(execute_func(**kwargs))

            self.call_count += 1
            self.last_called = fields.Datetime.now()
            return str(result)

        except asyncio.TimeoutError:
            self.error_count += 1
            raise UserError(_('Tool execution timed out after 30s'))

        except Exception as e:
            self.error_count += 1
            _logger.error("Tool '%s' execution failed: %s", self.name, e, exc_info=True)
            raise UserError(_('Tool execution failed: %s') % str(e))

    def to_core_tool(self, env=None):
        """Convert to core.tools.Tool for use in AgentLoop.

        Returns a Tool dataclass instance compatible with the core loop.
        """
        from ..core.tools import Tool

        params = json.loads(self.parameters) if self.parameters else {
            "type": "object", "properties": {}, "required": []
        }

        async def handler(**kwargs):
            # Re-execute in Odoo context
            try:
                result = self._execute_tool(kwargs)
                return result
            except Exception as e:
                return f"Tool error ({self.name}): {e}"

        return Tool(
            name=self.name,
            description=self.description,
            parameters=params,
            handler=handler,
            risk_level=self.risk_level,
            source="custom",
        )
