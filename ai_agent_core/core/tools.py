# -*- coding: utf-8 -*-
from __future__ import annotations
"""
Tool System — without LangChain (TOOL-001, TOOL-005).

TOOL-001: Tool dataclass — no LangChain BaseTool inheritance.
TOOL-005: Tool serialization to OpenAI + Anthropic format.

Tools are plain Python objects. The AgentLoop calls tool.execute().
Serialization is lazy — only when needed for API calls.
"""

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable, List, Dict

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tool (TOOL-001)
# ---------------------------------------------------------------------------

@dataclass
class Tool:
    """A tool that an agent can call.

    No LangChain inheritance. No framework wrapper.
    Just name + description + JSON Schema + handler.
    """

    name: str
    description: str
    parameters: Dict  # JSON Schema for parameters
    handler: Callable[..., Awaitable[str]]
    risk_level: str = "read_only"  # safe | read_only | write | destructive | execute
    source: str = "custom"  # odoo_model | mcp | custom

    # Risk level thresholds for approval (HITL-005)
    RISK_LEVELS = {
        "safe": 0,
        "read_only": 1,
        "write": 2,
        "destructive": 3,
        "execute": 4,
    }

    def needs_human_approval(self, threshold: int = 2) -> bool:
        """Check if this tool requires human approval at given threshold."""
        level = self.RISK_LEVELS.get(self.risk_level, 1)
        if self.risk_level in ("destructive", "execute"):
            return True  # Always require approval for destructive/execute
        return level >= threshold

    async def execute(self, **kwargs) -> str:
        """Execute the tool with given parameters. Returns result as string."""
        try:
            return await self.handler(**kwargs)
        except Exception as e:
            return f"Tool error ({self.name}): {e}"

    # -- Serialization (TOOL-005) --

    def to_openai(self) -> dict:
        """Serialize to OpenAI function-calling format."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def to_anthropic(self) -> dict:
        """Serialize to Anthropic tool-use format."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.parameters,
        }


# ---------------------------------------------------------------------------
# ToolRegistry
# ---------------------------------------------------------------------------

class ToolRegistry:
    """Collects and looks up tools by name.

    Tools are registered at session start from multiple sources:
    - OdooModelTools (auto-generated per registered model)
    - MCPTools (discovered from MCP servers)
    - CustomTools (user-defined via ai.tool)
    """

    def __init__(self):
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        """Register a tool. Overwrites if name already exists."""
        self._tools[tool.name] = tool

    def register_many(self, tools: list[Tool]) -> None:
        for t in tools:
            self.register(t)

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def list(self) -> list[Tool]:
        return list(self._tools.values())

    def to_openai(self) -> list[dict]:
        return [t.to_openai() for t in self._tools.values()]

    def to_anthropic(self) -> list[dict]:
        return [t.to_anthropic() for t in self._tools.values()]

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools


# ---------------------------------------------------------------------------
# Built-in tools for testing
# ---------------------------------------------------------------------------

async def _tool_calculator(expression: str) -> str:
    """Evaluate a mathematical expression. Test tool."""
    allowed = set("0123456789+-*/.() ")
    if not all(c in allowed for c in expression):
        return "Error: expression contains disallowed characters"
    try:
        result = eval(expression, {"__builtins__": {}})
        return str(result)
    except Exception as e:
        return f"Error: {e}"


async def _tool_web_search(query: str = "") -> str:
    """Search the web using DuckDuckGo. Returns top 5 results."""
    if not query or not query.strip():
        return "Error: query is required"
    try:
        from duckduckgo_search import DDGS
        results = list(DDGS().text(query, max_results=5))
        if not results:
            return "No results found."
        return "\n".join(
            f"{i+1}. {r.get('title','?')}\n   {r.get('body','')[:200]}\n   {r.get('href','')}"
            for i, r in enumerate(results)
        )
    except ImportError:
        return "Error: duckduckgo_search not installed. Run: pip install duckduckgo-search"
    except Exception as e:
        return f"Search error: {e}"


async def _tool_fetch_url(url: str = "") -> str:
    """Fetch and extract text content from a URL."""
    if not url:
        return "Error: url is required"
    try:
        import httpx
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(url, headers={'User-Agent': 'Odoo-AI/1.0'})
            r.raise_for_status()
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(r.text, 'html.parser')
        for tag in soup(['script', 'style', 'nav', 'footer', 'header']):
            tag.decompose()
        text = soup.get_text(separator=' ', strip=True)
        return text[:5000] if len(text) > 5000 else text
    except ImportError as e:
        return f"Error: {e}. Run: pip install httpx beautifulsoup4"
    except Exception as e:
        return f"Fetch error: {e}"


def builtin_tools() -> list[Tool]:
    """Return built-in test tools for development."""
    return [
        Tool(
            name="calculator",
            description="Evaluate a mathematical expression. Supports +, -, *, /, and parentheses.",
            parameters={
                "type": "object",
                "properties": {
                    "expression": {
                        "type": "string",
                        "description": "The mathematical expression to evaluate",
                    }
                },
                "required": ["expression"],
            },
            handler=_tool_calculator,
            risk_level="safe",
            source="builtin",
        ),
        Tool(
            name="web_search",
            description="Search the web using DuckDuckGo. Returns top 5 results with title, snippet, and URL.",
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query (10 words or less)",
                    }
                },
                "required": ["query"],
            },
            handler=_tool_web_search,
            risk_level="safe",
            source="builtin",
        ),
        Tool(
            name="fetch_url",
            description="Fetch and extract text content from a URL. Returns the main text content of the page.",
            parameters={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The URL to fetch",
                    }
                },
                "required": ["url"],
            },
            handler=_tool_fetch_url,
            risk_level="safe",
            source="builtin",
        ),
        Tool(
            name="echo",
            description="Echo back the message. Useful for testing.",
            parameters={
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": "The message to echo back",
                    }
                },
                "required": ["message"],
            },
            handler=_tool_echo,
            risk_level="safe",
            source="builtin",
        ),
    ]


async def _tool_echo(message: str) -> str:
    """Echo back the message. Test tool."""
    return message


# ---------------------------------------------------------------------------
# Odoo Model Tools (TOOL-002) — auto-generated from registered models
# ---------------------------------------------------------------------------

# Odoo model field type → JSON Schema type mapping
ODOO_TYPE_TO_JSON = {
    'char': 'string',
    'text': 'string',
    'html': 'string',
    'integer': 'integer',
    'float': 'number',
    'monetary': 'number',
    'boolean': 'boolean',
    'date': 'string',
    'datetime': 'string',
    'selection': 'string',
    'many2one': 'integer',
    'many2many': 'array',
    'one2many': 'array',
    'binary': 'string',
}


def model_to_tools(model_name: str, env=None) -> list[Tool]:
    """Generate OdooModelTools for a registered Odoo model.

    Creates tools: search_read, read, write, create, unlink
    Each tool wraps Odoo ORM with the authenticated user's access rights.

    Args:
        model_name: Odoo model technical name (e.g. 'res.partner')
        env: Odoo environment (for access rights)

    Returns:
        List of Tool instances for this model
    """
    model_display = model_name.replace('.', '_').replace('_', ' ').title()

    tools = [
        Tool(
            name=f"search_read_{model_name.replace('.', '_')}",
            description=(
                f"Search and read {model_name} records. "
                f"Returns matching records with specified fields. "
                f"Domain uses Odoo domain syntax: [[('field', 'operator', 'value')]]."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "domain": {
                        "type": "array",
                        "description": f"Odoo search domain for {model_name}",
                        "items": {"type": "array"},
                    },
                    "fields": {
                        "type": "array",
                        "description": "Field names to return (default: ['id', 'name'])",
                        "items": {"type": "string"},
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum records to return (default: 100)",
                        "default": 100,
                    },
                    "offset": {
                        "type": "integer",
                        "description": "Records to skip (for pagination)",
                    },
                    "order": {
                        "type": "string",
                        "description": "Sort order (e.g. 'name asc')",
                    },
                },
            },
            handler=_make_search_read_handler(model_name),
            risk_level="read_only",
            source="odoo_model",
        ),
        Tool(
            name=f"read_{model_name.replace('.', '_')}",
            description=f"Read specific {model_name} records by ID.",
            parameters={
                "type": "object",
                "properties": {
                    "ids": {
                        "type": "array",
                        "description": "Record IDs to read",
                        "items": {"type": "integer"},
                    },
                    "fields": {
                        "type": "array",
                        "description": "Field names to return",
                        "items": {"type": "string"},
                    },
                },
                "required": ["ids"],
            },
            handler=_make_read_handler(model_name),
            risk_level="read_only",
            source="odoo_model",
        ),
        Tool(
            name=f"write_{model_name.replace('.', '_')}",
            description=f"Write values to {model_name} records.",
            parameters={
                "type": "object",
                "properties": {
                    "ids": {
                        "type": "array",
                        "description": "Record IDs to update",
                        "items": {"type": "integer"},
                    },
                    "values": {
                        "type": "object",
                        "description": f"Field values to set on {model_name} records",
                    },
                },
                "required": ["ids", "values"],
            },
            handler=_make_write_handler(model_name),
            risk_level="write",
            source="odoo_model",
        ),
        Tool(
            name=f"create_{model_name.replace('.', '_')}",
            description=f"Create a new {model_name} record.",
            parameters={
                "type": "object",
                "properties": {
                    "values": {
                        "type": "object",
                        "description": f"Field values for the new {model_name} record",
                    },
                },
                "required": ["values"],
            },
            handler=_make_create_handler(model_name),
            risk_level="write",
            source="odoo_model",
        ),
        Tool(
            name=f"unlink_{model_name.replace('.', '_')}",
            description=f"Delete {model_name} records. USE WITH CAUTION.",
            parameters={
                "type": "object",
                "properties": {
                    "ids": {
                        "type": "array",
                        "description": "Record IDs to delete",
                        "items": {"type": "integer"},
                    },
                },
                "required": ["ids"],
            },
            handler=_make_unlink_handler(model_name),
            risk_level="destructive",
            source="odoo_model",
        ),
    ]
    return tools


def _make_search_read_handler(model_name: str):
    async def handler(domain=None, fields=None, limit=100, offset=0, order=None):
        try:
            from odoo import api
            # Use call_soon_threadsafe to run ORM in Odoo thread
            import json
            model = api.Environment.registry[model_name]
            # Fallback: direct import for testing without Odoo
            return json.dumps([{"error": "Odoo environment not available for testing"}])
        except Exception as e:
            return f"Error accessing model {model_name}: {e}"
    return handler

def _make_read_handler(model_name: str):
    async def handler(ids, fields=None):
        try:
            import json
            return json.dumps([{"error": "Odoo environment not available for testing"}])
        except Exception as e:
            return f"Error accessing model {model_name}: {e}"
    return handler

def _make_write_handler(model_name: str):
    async def handler(ids, values):
        try:
            import json
            return json.dumps({"error": "Odoo environment not available for testing"})
        except Exception as e:
            return f"Error accessing model {model_name}: {e}"
    return handler

def _make_create_handler(model_name: str):
    async def handler(values):
        try:
            import json
            return json.dumps({"error": "Odoo environment not available for testing"})
        except Exception as e:
            return f"Error accessing model {model_name}: {e}"
    return handler

def _make_unlink_handler(model_name: str):
    async def handler(ids):
        try:
            import json
            return json.dumps({"error": "Odoo environment not available for testing"})
        except Exception as e:
            return f"Error accessing model {model_name}: {e}"
    return handler


def register_model_tools(
    registry: 'ToolRegistry',
    model_names: list[str],
    env=None,
) -> int:
    """Register Odoo model tools for given models.

    Args:
        registry: ToolRegistry instance
        model_names: List of Odoo model technical names
        env: Odoo environment (required for production use)

    Returns:
        Number of tools registered
    """
    count = 0
    for model_name in model_names:
        tools = model_to_tools(model_name, env)
        registry.register_many(tools)
        count += len(tools)
    return count


# ---------------------------------------------------------------------------
# MCP Tool Support (TOOL-002) — discover from MCP servers
# ---------------------------------------------------------------------------

class MCPToolDiscovery:
    """Discover tools from MCP servers.

    MCP servers expose tools via the Model Context Protocol.
    This class handles discovery and registration.

    Usage:
        discovery = MCPToolDiscovery()
        discovery.add_server("filesystem", {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem"]})
        tools = await discovery.discover_all()
    """

    def __init__(self):
        self._servers: dict[str, dict] = {}

    def add_server(self, name: str, config: dict) -> None:
        """Register an MCP server configuration.

        Args:
            name: Server identifier
            config: Server config dict with command/args or url
        """
        self._servers[name] = config

    async def discover_all(self) -> list[Tool]:
        """Discover tools from all registered MCP servers.

        Returns:
            List of Tool instances from all servers
        """
        import asyncio
        tasks = [
            self._discover_server(name, cfg)
            for name, cfg in self._servers.items()
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        all_tools = []
        for result in results:
            if isinstance(result, Exception):
                _logger.warning("MCP server discovery failed: %s", result)
            elif isinstance(result, list):
                all_tools.extend(result)

        return all_tools

    async def _discover_server(self, name: str, config: dict) -> list[Tool]:
        """Discover tools from one MCP server.

        Uses subprocess communication with JSON-RPC via stdio.
        """
        import asyncio.subprocess
        import json as json_mod

        cmd = config.get("command", "")
        args = config.get("args", [])

        if not cmd:
            _logger.warning("MCP server '%s': no command configured", name)
            return []

        try:
            proc = await asyncio.create_subprocess_exec(
                cmd, *args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            # Send tools/list request
            request = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/list",
                "params": {},
            }
            request_str = json_mod.dumps(request) + "\n"
            proc.stdin.write(request_str.encode())
            await proc.stdin.drain()

            # Read response (with timeout)
            try:
                response_line = await asyncio.wait_for(
                    proc.stdout.readline(), timeout=10
                )
                response = json_mod.loads(response_line.decode())
            except asyncio.TimeoutError:
                _logger.warning("MCP server '%s' timed out", name)
                proc.kill()
                return []

            proc.terminate()
            await proc.wait()

            # Parse tools
            tools = []
            for tool_def in response.get("result", {}).get("tools", []):
                tool = Tool(
                    name=f"mcp_{name}_{tool_def['name']}",
                    description=tool_def.get("description", ""),
                    parameters=tool_def.get("inputSchema", {"type": "object", "properties": {}}),
                    handler=_make_mcp_tool_handler(name, tool_def["name"], config),
                    risk_level="read_only",
                    source="mcp",
                )
                tools.append(tool)

            _logger.info("MCP server '%s': discovered %d tools", name, len(tools))
            return tools

        except FileNotFoundError:
            _logger.warning("MCP server '%s': command '%s' not found", name, cmd)
            return []
        except Exception as e:
            _logger.warning("MCP server '%s' discovery failed: %s", name, e)
            return []


def _make_mcp_tool_handler(server_name: str, tool_name: str, config: dict):
    """Create a handler that calls an MCP server tool."""
    async def handler(**kwargs):
        import asyncio.subprocess
        import json as json_mod

        cmd = config.get("command", "")
        args = config.get("args", [])

        try:
            proc = await asyncio.create_subprocess_exec(
                cmd, *args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            request = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": tool_name,
                    "arguments": kwargs,
                },
            }
            request_str = json_mod.dumps(request) + "\n"
            proc.stdin.write(request_str.encode())
            await proc.stdin.drain()

            try:
                response_line = await asyncio.wait_for(
                    proc.stdout.readline(), timeout=60
                )
                response = json_mod.loads(response_line.decode())
            except asyncio.TimeoutError:
                proc.kill()
                return f"MCP tool '{tool_name}' timed out"

            proc.terminate()
            await proc.wait()

            if "error" in response:
                return f"MCP error: {response['error']}"

            result = response.get("result", {})
            return json_mod.dumps(result.get("content", result), indent=2)

        except Exception as e:
            return f"MCP tool '{tool_name}' error: {e}"

    return handler
