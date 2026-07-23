# -*- coding: utf-8 -*-
"""
Tool System — without LangChain (TOOL-001, TOOL-005).

TOOL-001: Tool dataclass — no LangChain BaseTool inheritance.
TOOL-005: Tool serialization to OpenAI + Anthropic format.

Tools are plain Python objects. The AgentLoop calls tool.execute().
Serialization is lazy — only when needed for API calls.
"""

from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable

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
    parameters: dict  # JSON Schema for parameters
    handler: Callable[..., Awaitable[str]]
    risk_level: str = "read_only"  # safe | read_only | write | destructive | execute
    source: str = "custom"  # odoo_model | mcp | custom

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


async def _tool_echo(message: str) -> str:
    """Echo back the message. Test tool."""
    return message


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
