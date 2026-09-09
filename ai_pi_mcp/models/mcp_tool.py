# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.
"""Tool registry and ``@mcp_tool`` decorator.

Tools are plain methods on models that ``_inherit`` ``mcp.tool.mixin``. The
decorator records their MCP metadata (name, description, input schema) on the
``mcp.tool`` model so the ``tools/list`` handler can advertise them and
``tools/call`` can dispatch to them.

A minimal instance of the registry is kept in ``ai_pi_mcp.dev_tools`` so a
controller can look up a tool without instantiating the mixin model.
"""

import functools
import logging

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

# Tools whose execution mutates state (used by the controller to enforce a
# read-only scope before a write-capable tool is reached).
_WRITE_TOOLS = frozenset({
    "view_set_arch",
    "view_restore",
    "action_set_domain",
    "action_set_view_mode",
    "action_set_order",
    "module_install",
    "module_uninstall",
    "module_upgrade",
    "create_record",
    "update_record",
    "delete_record",
    "call_method",
})

# In-memory registry: tool name -> callable (the wrapped method).
_TOOL_REGISTRY = {}


def mcp_tool(name, description='', input_schema=None):
    """Decorate a method on an ``mcp.tool.mixin`` model as an MCP tool.

    :param name: the MCP tool name (advertised to clients).
    :param description: human description shown to the client.
    :param input_schema: JSON Schema (dict) describing the ``arguments``.
    """

    def decorator(func):
        @functools.wraps(func)
        def wrapper(self, *args, **kwargs):
            return func(self, *args, **kwargs)

        # Record which model the tool method is defined on so ``mcp.tool.call``
        # can resolve the right recordset to invoke it on. All tools in this
        # module live on ``ai_pi_mcp.dev_tools``.
        wrapper.__mcp_owner_model__ = 'ai_pi_mcp.dev_tools'
        wrapper._mcp_tool = {
            'name': name,
            'description': description,
            'input_schema': input_schema or {'type': 'object', 'properties': {}},
        }
        _TOOL_REGISTRY[name] = wrapper
        _logger.info('ai_pi_mcp: registered MCP tool %r', name)
        return wrapper

    return decorator


class McpToolMixin(models.AbstractModel):
    """Abstract model whose concrete subclasses may declare ``@mcp_tool``s."""

    _name = 'mcp.tool.mixin'
    _description = 'MCP Tool Mixin'


class McpTool(models.Model):
    _name = 'mcp.tool'
    _description = 'MCP Tool Registry'
    _rec_name = 'name'

    name = fields.Char(string='Tool Name', readonly=True)
    description = fields.Text(string='Description', readonly=True)
    input_schema = fields.Text(string='Input Schema (JSON)', readonly=True)

    @api.model
    def get_tools(self):
        """Return the MCP tool list (name/description/inputSchema) for tools/list."""
        tools = []
        for name, func in _TOOL_REGISTRY.items():
            meta = getattr(func, '_mcp_tool', {})
            tools.append({
                'name': name,
                'description': meta.get('description', ''),
                'inputSchema': meta.get('input_schema', {'type': 'object'}),
            })
        return tools

    @api.model
    def call(self, name, arguments, env, enforce_scope=None):
        """Dispatch a ``tools/call`` to the registered tool.

        Tools run in the caller's Odoo user context (``env``) so the standard
        access rules apply. ``enforce_scope`` (read / read_write) is applied by
        the controller before a write-capable tool is reached.
        """
        func = _TOOL_REGISTRY.get(name)
        if func is None:
            raise ValueError('Unknown tool: %s' % name)
        # The tool methods live on ``ai_pi_mcp.dev_tools``. Look up the model
        # the decorated method is bound to and call it there.
        model = getattr(func, '__mcp_owner_model__', None)
        if not model or model not in env:
            # Fall back to the concrete dev-tools abstract model.
            model = 'ai_pi_mcp.dev_tools'
        record = env[model]
        method = func.__name__
        return getattr(record, method)(arguments or {})
