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
        Tool(
            name="nats_publish",
            description="Publish a message to a NATS subject for Pi workers to consume. "
                        "Use this to delegate infrastructure tasks (bash, deployment, monitoring) "
                        "to remote Pi agents. Returns acknowledgment or error.",
            parameters={
                "type": "object",
                "properties": {
                    "subject": {
                        "type": "string",
                        "description": "NATS subject to publish to, e.g. 'pi.task.new' or 'pi.task.assign.{host}'",
                    },
                    "task_id": {
                        "type": "integer",
                        "description": "Unique task ID for tracking the result",
                    },
                    "prompt": {
                        "type": "string",
                        "description": "The task prompt for the worker to execute",
                    },
                    "skills": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of skill names the worker should load (optional)",
                    },
                },
                "required": ["subject", "task_id", "prompt"],
            },
            handler=_tool_nats_publish,
            risk_level="execute",
            source="builtin",
        ),

        # ── Quest Builder: Inventory tools (read_only, safe) ──
        Tool(
            name="inventory_architecture",
            description="Return complete system architecture: all ai.* models with fields/relations, init_types, and MODULE.md docs from installed ai_* modules.",
            parameters={"type": "object", "properties": {}, "required": []},
            handler=_tool_inventory_architecture,
            risk_level="safe",
            source="builtin",
        ),
        Tool(
            name="inventory_quests",
            description="List all active ai.quest records with name, status, and init_type.",
            parameters={"type": "object", "properties": {}, "required": []},
            handler=_tool_inventory_quests,
            risk_level="safe",
            source="builtin",
        ),
        Tool(
            name="inventory_agents",
            description="List all ai.agent records with name, model, and assigned skills.",
            parameters={"type": "object", "properties": {}, "required": []},
            handler=_tool_inventory_agents,
            risk_level="safe",
            source="builtin",
        ),
        Tool(
            name="inventory_skills",
            description="List all ai.skill records with name, category, and trigger keywords.",
            parameters={"type": "object", "properties": {}, "required": []},
            handler=_tool_inventory_skills,
            risk_level="safe",
            source="builtin",
        ),
        Tool(
            name="inventory_models",
            description="List all ai.model records with capabilities: is_vision, has_streaming, context_window, provider.",
            parameters={"type": "object", "properties": {}, "required": []},
            handler=_tool_inventory_models,
            risk_level="safe",
            source="builtin",
        ),
        Tool(
            name="inventory_tools",
            description="List all ai.tool records with name and risk_level.",
            parameters={"type": "object", "properties": {}, "required": []},
            handler=_tool_inventory_tools,
            risk_level="safe",
            source="builtin",
        ),
        Tool(
            name="inventory_identities",
            description="List all ai.identity templates with name and scope.",
            parameters={"type": "object", "properties": {}, "required": []},
            handler=_tool_inventory_identities,
            risk_level="safe",
            source="builtin",
        ),
        Tool(
            name="inventory_odoo_models",
            description="List available Odoo models (ir.model) for powerbox binding.",
            parameters={"type": "object", "properties": {}, "required": []},
            handler=_tool_inventory_odoo_models,
            risk_level="safe",
            source="builtin",
        ),

        # ── Quest Builder: Execution tools (write, destructive) ──
        Tool(
            name="builder_create_quest",
            description="Create a new ai.quest record. Params: name (required), description, init_types (list), is_supervisor (bool). Returns quest ID.",
            parameters={"type": "object", "properties": {
                "name": {"type": "string", "description": "Quest name"},
                "description": {"type": "string", "description": "System prompt / quest description"},
                "init_types": {"type": "string", "description": "Comma-separated init types: web_ui,chat,powerbox,mail,cron,server_action,manual"},
                "is_supervisor": {"type": "boolean", "description": "Enable supervisor mode for multi-agent orchestration"},
            }, "required": ["name"]},
            handler=_tool_builder_create_quest,
            risk_level="destructive",
            source="builtin",
        ),
        Tool(
            name="builder_update_quest",
            description="Update an existing ai.quest. Params: quest_id (required), then any of: name, description, is_supervisor.",
            parameters={"type": "object", "properties": {
                "quest_id": {"type": "integer", "description": "Quest ID to update"},
                "name": {"type": "string"},
                "description": {"type": "string"},
                "is_supervisor": {"type": "boolean"},
            }, "required": ["quest_id"]},
            handler=_tool_builder_update_quest,
            risk_level="destructive",
            source="builtin",
        ),
        Tool(
            name="builder_create_agent",
            description="Create a new ai.agent. Params: name (required), description, model (bifrost model name), skills (comma-separated skill names). Returns agent ID.",
            parameters={"type": "object", "properties": {
                "name": {"type": "string", "description": "Agent name"},
                "description": {"type": "string", "description": "Agent description / role"},
                "model": {"type": "string", "description": "Bifrost model, e.g. cerebras/gpt-oss-120b or anthropic/claude-sonnet-4"},
                "skills": {"type": "string", "description": "Comma-separated skill names to assign (must exist)"},
            }, "required": ["name"]},
            handler=_tool_builder_create_agent,
            risk_level="destructive",
            source="builtin",
        ),
        Tool(
            name="builder_create_skill",
            description="Create a new ai.skill. Params: name (required), category, recipe_text (full markdown recipe), trigger_keywords.",
            parameters={"type": "object", "properties": {
                "name": {"type": "string", "description": "Skill name"},
                "category": {"type": "string", "description": "Category: general, analysis, accounting, development, infrastructure, communication, research"},
                "recipe_text": {"type": "string", "description": "Full markdown recipe/instructions for this skill"},
                "trigger_keywords": {"type": "string", "description": "Comma-separated keywords that trigger this skill"},
            }, "required": ["name", "recipe_text"]},
            handler=_tool_builder_create_skill,
            risk_level="destructive",
            source="builtin",
        ),
        Tool(
            name="builder_assign_agent",
            description="Assign an agent to a quest. Params: quest_id, agent_id, sequence (optional).",
            parameters={"type": "object", "properties": {
                "quest_id": {"type": "integer"},
                "agent_id": {"type": "integer"},
                "sequence": {"type": "integer", "description": "Order in the agent pipeline (1-based)"},
            }, "required": ["quest_id", "agent_id"]},
            handler=_tool_builder_assign_agent,
            risk_level="destructive",
            source="builtin",
        ),
        Tool(
            name="builder_configure_init_type",
            description="Configure an init type on a quest. Params: quest_id, init_type (one of: web_ui,chat,channel,mail,cron,server_action,powerbox,manual,openai_api), config_json (type-specific config).",
            parameters={"type": "object", "properties": {
                "quest_id": {"type": "integer"},
                "init_type": {"type": "string", "description": "Init type to configure"},
                "config": {"type": "string", "description": "JSON config: for powerbox use {\"model_ids\": [\"sale.order\",\"crm.lead\"]}, for mail use {\"alias_name\":\"support\"}"},
            }, "required": ["quest_id", "init_type"]},
            handler=_tool_builder_configure_init_type,
            risk_level="destructive",
            source="builtin",
        ),
        Tool(
            name="builder_link_skill_to_agent",
            description="Link an existing skill to an agent. Params: agent_id, skill_id.",
            parameters={"type": "object", "properties": {
                "agent_id": {"type": "integer"},
                "skill_id": {"type": "integer"},
            }, "required": ["agent_id", "skill_id"]},
            handler=_tool_builder_link_skill_to_agent,
            risk_level="destructive",
            source="builtin",
        ),

        # ── Skill Builder tools ──
        Tool(
            name="read_skill",
            description="Read the full recipe_text from an ai.skill record. Params: skill_id.",
            parameters={"type": "object", "properties": {
                "skill_id": {"type": "integer"},
            }, "required": ["skill_id"]},
            handler=_tool_read_skill,
            risk_level="safe",
            source="builtin",
        ),
        Tool(
            name="search_github_skills",
            description="Search GitHub for SKILL.md files matching a query. Returns repos, paths, and raw URLs.",
            parameters={"type": "object", "properties": {
                "query": {"type": "string"},
            }, "required": ["query"]},
            handler=_tool_search_github_skills,
            risk_level="safe",
            source="builtin",
        ),
        Tool(
            name="builder_draft_skill",
            description="Create a temporary draft skill for testing. Params: name (required), recipe_text, category, trigger_keywords.",
            parameters={"type": "object", "properties": {
                "name": {"type": "string"},
                "recipe_text": {"type": "string"},
                "category": {"type": "string"},
                "trigger_keywords": {"type": "string"},
            }, "required": ["name", "recipe_text"]},
            handler=_tool_builder_draft_skill,
            risk_level="destructive",
            source="builtin",
        ),
        Tool(
            name="builder_test_skill",
            description="Test a skill by running a prompt through a temporary quest. Params: skill_id, prompt. Returns AI response.",
            parameters={"type": "object", "properties": {
                "skill_id": {"type": "integer"},
                "prompt": {"type": "string"},
            }, "required": ["skill_id", "prompt"]},
            handler=_tool_builder_test_skill,
            risk_level="safe",
            source="builtin",
        ),
        Tool(
            name="builder_create_skill",
            description="Save a skill permanently. Params: name (required), recipe_text, category, trigger_keywords, description.",
            parameters={"type": "object", "properties": {
                "name": {"type": "string"},
                "recipe_text": {"type": "string"},
                "category": {"type": "string"},
                "trigger_keywords": {"type": "string"},
                "description": {"type": "string"},
            }, "required": ["name", "recipe_text"]},
            handler=_tool_builder_create_skill,
            risk_level="destructive",
            source="builtin",
        ),
        Tool(
            name="builder_improve_skill",
            description="Update a skill based on user feedback. Params: skill_id, feedback, field.",
            parameters={"type": "object", "properties": {
                "skill_id": {"type": "integer"},
                "feedback": {"type": "string"},
                "field": {"type": "string", "description": "Field: recipe_text, trigger_keywords, description, category"},
            }, "required": ["skill_id", "feedback"]},
            handler=_tool_builder_improve_skill,
            risk_level="destructive",
            source="builtin",
        ),
    ]


async def _tool_echo(message: str) -> str:
    """Echo back the message. Test tool."""
    return message


# Global NATS connection (lazy-init)
_nats_connection = None


async def _tool_nats_publish(subject: str, task_id: int, prompt: str,
                              skills: list[str] | None = None) -> str:
    """Publish a task to NATS for Pi workers.

    Lazy-initializes the NATS connection on first call.
    Times out after 5 seconds if NATS is unreachable.
    """
    global _nats_connection
    import os
    import asyncio

    try:
        # Lazy init: connect on first use
        if _nats_connection is None:
            from nats import connect as nats_connect
            nats_url = os.environ.get('NATS_URL', 'nats://localhost:4222')
            try:
                _nats_connection = await asyncio.wait_for(
                    nats_connect(nats_url), timeout=5.0
                )
            except asyncio.TimeoutError:
                return f'NATS publish failed: connection timeout to {nats_url}'
            except Exception as e:
                _nats_connection = None
                return f'NATS publish failed: {e}'

        if _nats_connection.is_closed:
            _nats_connection = None
            return 'NATS publish failed: connection closed'

        # Build payload
        payload = {
            'task_id': task_id,
            'prompt': prompt,
        }
        if skills:
            payload['skills'] = skills

        import json as json_mod
        data = json_mod.dumps(payload).encode()
        await _nats_connection.publish(subject, data)

        return f'Published to \'{subject}\' ({len(data)} bytes)'

    except ImportError:
        return 'NATS publish failed: nats package not installed. Install with: pip install nats-py'
    except Exception as e:
        _nats_connection = None
        return f'NATS publish failed: {e}'


# ---------------------------------------------------------------------------
# TodoList — plan-before-action (inspired by OpenWorker)
# ---------------------------------------------------------------------------


from dataclasses import dataclass as dc_dataclass


@dc_dataclass
class TodoList:
    """A structured task list the agent maintains.

    Rendered by the UI as a progress indicator. The agent calls todo_write
    to replace the entire list each time — never append/remove individual items.
    """
    items: list[dict] = None

    def __post_init__(self):
        if self.items is None:
            self.items = []

    @property
    def done_count(self) -> int:
        return sum(1 for item in self.items if item.get("status") == "done")

    @property
    def total_count(self) -> int:
        return len(self.items)

    @property
    def progress_pct(self) -> float:
        if not self.items:
            return 0.0
        return self.done_count / self.total_count * 100


async def _tool_todo_write(todos: list = None, items: list = None) -> str:
    """Replace the task list. Each item has 'content' and 'status'.

    Status must be one of: pending, in_progress, done.
    The list replaces any previous list — provide ALL items each call.
    """
    import json
    from .tools import _TODO_HOLDER

    normalized = []
    for entry in (todos if todos is not None else items) or []:
        if isinstance(entry, dict):
            status = entry.get("status", "pending")
            if status == "completed":
                status = "done"
            if status not in ("pending", "in_progress", "done"):
                status = "pending"
            normalized.append({
                "content": str(entry.get("content", "")),
                "status": status,
            })
        else:
            normalized.append({"content": str(entry), "status": "pending"})

    if _TODO_HOLDER is not None:
        _TODO_HOLDER.items = normalized
    return json.dumps({"count": len(normalized), "todos": normalized})


def _tool_propose_plan(plan: str = "") -> str:
    """Propose a plan. In PLAN mode this pauses for approval before writes execute.

    This is a sync function that returns immediately. The actual approval
    is handled by the AgentLoop which intercepts this tool call before execution.

    The plan is rendered for the user and they approve/deny. Approval flips the
    permission mode from PLAN to INTERACTIVE (or AUTO).
    """
    import json
    return json.dumps({
        "plan_received": bool(plan),
        "status": "pending_approval",
        "note": "Waiting for user to approve this plan before proceeding.",
    })


# Module-level holder for todo state — set by AgentLoop
_TODO_HOLDER: TodoList | None = None

# Module-level skill source — set by AgentLoop or Odoo environment
_SKILL_SOURCE: callable | None = None  # async (name: str) -> dict


async def _tool_load_skill(name: str = "") -> str:
    """Load a skill's full instructions by name. Call this when a skill from
the catalog is relevant to the current task."""
    import json
    if not name or not name.strip():
        return json.dumps({"error": "skill name is required"})

    if _SKILL_SOURCE is None:
        return json.dumps({
            "error": "no skill source configured",
            "note": "This environment doesn't support dynamic skill loading.",
        })

    try:
        result = await _SKILL_SOURCE(name.strip())
        return json.dumps(result, default=str)
    except Exception as e:
        return json.dumps({"error": f"failed to load skill '{name}': {e}"})


def skill_tools(skill_source: callable | None = None) -> list[Tool]:
    """Return skill-related tools: load_skill.

    Args:
        skill_source: Async callable that takes a skill name and returns
                      a dict with name, instructions, resources_path etc.
                      If None, load_skill returns an error.
    """
    global _SKILL_SOURCE
    if skill_source is not None:
        _SKILL_SOURCE = skill_source

    return [
        Tool(
            name="load_skill",
            description=(
                "Load a skill's full instructions and resources by name. "
                "Use this when a skill from the available skills catalog "
                "is relevant to the current task. Returns the skill's "
                "complete instructions and any resource paths."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "The skill name to load (from the catalog)",
                    },
                },
                "required": ["name"],
            },
            handler=_tool_load_skill,
            risk_level="safe",
            source="builtin",
        ),
    ]


def planning_tools(todo_holder: TodoList | None = None) -> list[Tool]:
    """Return plan-before-action tools: todo_write and propose_plan.

    Args:
        todo_holder: A TodoList instance to hold the agent's task state.
                     If None, a module-level holder is used.
    """
    global _TODO_HOLDER
    if todo_holder is not None:
        _TODO_HOLDER = todo_holder
    elif _TODO_HOLDER is None:
        _TODO_HOLDER = TodoList()

    return [
        Tool(
            name="todo_write",
            description=(
                "Replace the task list. Provide the FULL list of todos each call "
                "— never incrementally append. Each todo is an object with: "
                "'content' (string describing the step) and 'status' "
                "(one of: pending, in_progress, done). Always keep exactly one "
                "item in_progress and update statuses as you finish steps. "
                "Start every complex task with todo_write."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "todos": {
                        "type": "array",
                        "description": "The full list of todos",
                        "items": {
                            "type": "object",
                            "properties": {
                                "content": {
                                    "type": "string",
                                    "description": "What needs to be done",
                                },
                                "status": {
                                    "type": "string",
                                    "enum": ["pending", "in_progress", "done"],
                                    "description": "Current status of this step",
                                },
                            },
                            "required": ["content", "status"],
                        },
                    },
                },
                "required": ["todos"],
            },
            handler=_tool_todo_write,
            risk_level="safe",
            source="builtin",
        ),
        Tool(
            name="propose_plan",
            description=(
                "Propose a plan for the user to review before executing. "
                "Call this with a clear, structured description of what you "
                "plan to do — what steps, what changes, what tools you'll use. "
                "The user will review and approve/deny. Use this in PLAN mode "
                "after you've finished exploring and are ready to make changes."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "plan": {
                        "type": "string",
                        "description": (
                            "A structured plan describing what you intend to do, "
                            "including which tools you'll use and what changes "
                            "you'll make."
                        ),
                    },
                },
                "required": ["plan"],
            },
            handler=_tool_propose_plan,
            risk_level="safe",
            source="builtin",
        ),
    ]


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
# Quest Builder Tool Handlers
# ---------------------------------------------------------------------------

def _tool_inventory_architecture(env, **kwargs):
    """Return complete system architecture via runtime introspection + MODULE.md."""
    import json as _json, os as _os
    result = {"models": {}, "init_types": [], "modules": {}}

    # Runtime introspection: all ai.* models
    for model_name in sorted(env.registry._fields):
        if not model_name.startswith("ai."):
            continue
        Model = env[model_name]
        fields_info = {}
        for fname, field in Model._fields.items():
            if fname.startswith("_"):
                continue
            info = {"type": field.type}
            if hasattr(field, "required"):
                info["required"] = field.required
            if hasattr(field, "help") and field.help:
                info["help"] = field.help[:200]
            if hasattr(field, "comodel_name") and field.comodel_name:
                info["relation"] = field.comodel_name
            fields_info[fname] = info
        result["models"][model_name] = {
            "description": getattr(Model, "_description", ""),
            "fields": fields_info,
        }

    # Init types
    if hasattr(env["ai.quest.init_type"], "INIT_TYPE_SELECTION"):
        result["init_types"] = [
            {"key": k, "label": v}
            for k, v in env["ai.quest.init_type"].INIT_TYPE_SELECTION
        ]

    # MODULE.md files from ai_* modules
    addons_paths = getattr(__import__("odoo").modules.module, "get_module_root", lambda x: None)
    search_dirs = [
        "/usr/share/odoo-ai",
        "/usr/lib/python3/dist-packages/odoo/addons",
    ]
    for base_dir in search_dirs:
        if not _os.path.isdir(base_dir):
            continue
        for entry in _os.listdir(base_dir):
            if not entry.startswith("ai_"):
                continue
            md_path = _os.path.join(base_dir, entry, "MODULE.md")
            if _os.path.isfile(md_path):
                try:
                    with open(md_path, "r") as f:
                        result["modules"][entry] = f.read()[:4000]
                except Exception:
                    pass

    return _json.dumps(result, indent=2, default=str)


def _tool_inventory_quests(env, **kwargs):
    import json as _json
    quests = env["ai.quest"].search([("active", "=", True)])
    data = [{"id": q.id, "name": q.name, "status": q.status,
             "init_type": q.init_type, "is_supervisor": q.is_supervisor,
             "agent_count": q.agent_count}
            for q in quests]
    return _json.dumps(data, indent=2)


def _tool_inventory_agents(env, **kwargs):
    import json as _json
    agents = env["ai.agent"].search([])
    data = []
    for a in agents:
        skills = [s.name for s in a.skill_ids]
        model_name = a.bifrost_model or a.direct_model or "none"
        data.append({"id": a.id, "name": a.name, "model": model_name,
                     "provider_type": a.provider_type, "skills": skills})
    return _json.dumps(data, indent=2)


def _tool_inventory_skills(env, **kwargs):
    import json as _json
    skills = env["ai.skill"].search([])
    data = [{"id": s.id, "name": s.name, "category": s.category or "general",
             "trigger_keywords": s.trigger_keywords or "",
             "description": (s.description or "")[:200]}
            for s in skills]
    return _json.dumps(data, indent=2)


def _tool_inventory_models(env, **kwargs):
    import json as _json
    models = env["ai.model"].search([("active", "=", True)])
    data = [{"id": m.id, "name": m.name, "display_name": m.display_name,
             "is_vision": m.is_vision, "has_streaming": m.has_streaming,
             "context_window": m.context_window,
             "provider": m.provider_id.name if m.provider_id else "unknown"}
            for m in models]
    return _json.dumps(data, indent=2)


def _tool_inventory_tools(env, **kwargs):
    import json as _json
    tools = env["ai.tool"].search([])
    data = [{"id": t.id, "name": t.name, "risk_level": t.risk_level,
             "description": (t.description or "")[:200]}
            for t in tools]
    return _json.dumps(data, indent=2)


def _tool_inventory_identities(env, **kwargs):
    import json as _json
    identities = env["ai.identity"].search([("is_template", "=", True)])
    data = [{"id": i.id, "name": i.name, "scope": i.scope,
             "description": (i.description or "")[:200]}
            for i in identities]
    return _json.dumps(data, indent=2)


def _tool_inventory_odoo_models(env, **kwargs):
    import json as _json
    models = env["ir.model"].search([("transient", "=", False)])
    data = [{"id": m.id, "model": m.model, "name": m.name}
            for m in models[:200]]  # Limit to avoid huge responses
    return _json.dumps(data, indent=2)


def _tool_builder_create_quest(env, name, description="", init_types="", is_supervisor=False, **kwargs):
    """Create a new ai.quest. Returns quest ID."""
    vals = {
        "name": name,
        "description": description,
        "status": "active",
        "is_supervisor": bool(is_supervisor),
    }
    quest = env["ai.quest"].create(vals)

    # Configure init types
    if init_types:
        for itype in [t.strip() for t in init_types.split(",") if t.strip()]:
            env["ai.quest.init_type"].create({
                "quest_id": quest.id,
                "init_type": itype,
                "active": True,
            })

    return f"Quest #{quest.id} '{quest.name}' created with init_types: {init_types or 'none'}"


def _tool_builder_update_quest(env, quest_id, **kwargs):
    """Update an existing ai.quest. Only updates provided fields."""
    quest = env["ai.quest"].browse(int(quest_id))
    if not quest.exists():
        return f"Error: Quest #{quest_id} not found"
    updates = {}
    for field in ("name", "description"):
        if field in kwargs and kwargs[field]:
            updates[field] = kwargs[field]
    if "is_supervisor" in kwargs:
        updates["is_supervisor"] = bool(kwargs["is_supervisor"])
    if updates:
        quest.write(updates)
    return f"Quest #{quest.id} updated: {list(updates.keys())}"


def _tool_builder_create_agent(env, name, description="", model="cerebras/gpt-oss-120b", skills="", **kwargs):
    """Create a new ai.agent. Returns agent ID."""
    vals = {
        "name": name,
        "description": description,
        "provider_type": "bifrost",
        "bifrost_model": model,
        "status": "active",
    }
    agent = env["ai.agent"].create(vals)

    # Link skills
    if skills:
        skill_names = [s.strip() for s in skills.split(",") if s.strip()]
        skill_recs = env["ai.skill"].search([("name", "in", skill_names)])
        if skill_recs:
            agent.skill_ids = [(6, 0, skill_recs.ids)]
        found = {s.name for s in skill_recs}
        missing = set(skill_names) - found
        if missing:
            return (f"Agent #{agent.id} '{agent.name}' created. "
                    f"Skills linked: {list(found)}. "
                    f"Skills NOT found: {list(missing)} — create them first with builder_create_skill.")

    return f"Agent #{agent.id} '{agent.name}' created with model {model}"


def _tool_builder_create_skill(env, name, recipe_text, category="general", trigger_keywords="", **kwargs):
    """Create a new ai.skill. Returns skill ID."""
    skill = env["ai.skill"].create({
        "name": name,
        "description": recipe_text[:200],
        "recipe_text": recipe_text,
        "category": category,
        "trigger_keywords": trigger_keywords,
        "compatibility": "any",
    })
    return f"Skill #{skill.id} '{skill.name}' created (category: {category})"


def _tool_builder_assign_agent(env, quest_id, agent_id, sequence=10, **kwargs):
    """Assign an agent to a quest."""
    quest = env["ai.quest"].browse(int(quest_id))
    agent = env["ai.agent"].browse(int(agent_id))
    if not quest.exists():
        return f"Error: Quest #{quest_id} not found"
    if not agent.exists():
        return f"Error: Agent #{agent_id} not found"
    existing = env["ai.quest.agent"].search([
        ("quest_id", "=", quest.id),
        ("agent_id", "=", agent.id),
    ])
    if existing:
        return f"Agent '{agent.name}' already assigned to quest '{quest.name}'"
    env["ai.quest.agent"].create({
        "quest_id": quest.id,
        "agent_id": agent.id,
        "sequence": int(sequence),
    })
    return f"Agent '{agent.name}' assigned to quest '{quest.name}' (sequence: {sequence})"


def _tool_builder_configure_init_type(env, quest_id, init_type, config="{}", **kwargs):
    """Configure an init type on a quest."""
    import json as _json
    quest = env["ai.quest"].browse(int(quest_id))
    if not quest.exists():
        return f"Error: Quest #{quest_id} not found"
    config_data = _json.loads(config) if isinstance(config, str) else config
    vals = {
        "quest_id": quest.id,
        "init_type": init_type,
        "active": True,
    }
    if init_type == "powerbox" and "model_ids" in config_data:
        model_recs = env["ir.model"].search([
            ("model", "in", config_data["model_ids"])
        ])
        quest.write({"model_ids": [(6, 0, model_recs.ids)]})
    if init_type == "mail" and "alias_name" in config_data:
        vals["alias_name"] = config_data["alias_name"]
    if init_type == "web_ui":
        quest.write({"show_in_chat": True})
    # Let the init_type record auto-create resources (chat_user, alias, cron, etc.)
    init_record = env["ai.quest.init_type"].create(vals)
    init_record._after_change()
    return f"Init type '{init_type}' configured for quest '{quest.name}'"


def _tool_builder_link_skill_to_agent(env, agent_id, skill_id, **kwargs):
    """Link an existing skill to an agent."""
    agent = env["ai.agent"].browse(int(agent_id))
    skill = env["ai.skill"].browse(int(skill_id))
    if not agent.exists():
        return f"Error: Agent #{agent_id} not found"
    if not skill.exists():
        return f"Error: Skill #{skill_id} not found"
    agent.skill_ids = [(4, skill.id)]
    return f"Skill '{skill.name}' linked to agent '{agent.name}'"


# ── Skill Builder Tool Handlers ──

def _tool_read_skill(env, skill_id, **kwargs):
    """Read full recipe_text from an ai.skill record."""
    skill = env["ai.skill"].browse(int(skill_id))
    if not skill.exists():
        return f"Error: Skill #{skill_id} not found"
    return (
        f"# {skill.name}\n\n"
        f"Category: {skill.category or 'general'}\n"
        f"Triggers: {skill.trigger_keywords or '(none)'}\n"
        f"Description: {(skill.description or '')[:200]}\n\n"
        f"## Recipe\n\n{skill.recipe_text or '(no recipe)'}"
    )


def _tool_search_github_skills(env, query, **kwargs):
    """Search GitHub for SKILL.md files matching query."""
    import json as _json, urllib.request, urllib.parse
    q = urllib.parse.quote(f"{query} SKILL.md in:path")
    url = f"https://api.github.com/search/code?q={q}&per_page=10"
    try:
        req = urllib.request.Request(url)
        req.add_header("Accept", "application/vnd.github.v3+json")
        req.add_header("User-Agent", "odoo-ai-skill-builder")
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = _json.loads(resp.read())
        results = []
        for item in data.get("items", [])[:10]:
            raw_url = item["html_url"].replace("github.com", "raw.githubusercontent.com").replace("/blob/", "/")
            results.append({
                "repo": item["repository"]["full_name"],
                "path": item["path"],
                "raw_url": raw_url,
            })
        return _json.dumps(results, indent=2) if results else "No matching skills found on GitHub"
    except Exception as e:
        return _json.dumps({"error": str(e), "tip": "Try web_search instead if GitHub API is rate-limited"})


def _tool_builder_draft_skill(env, name, recipe_text, category="general", trigger_keywords="", **kwargs):
    """Create a temporary draft skill for testing."""
    skill = env["ai.skill"].create({
        "name": f"[DRAFT] {name}",
        "description": recipe_text[:200] if recipe_text else name,
        "recipe_text": recipe_text,
        "category": category,
        "trigger_keywords": trigger_keywords,
        "compatibility": "any",
        "active": False,  # Draft, not visible
    })
    return f"Draft skill #{skill.id} '{name}' created. Use builder_test_skill({skill.id}, prompt) to test it."


def _tool_builder_test_skill(env, skill_id, prompt, **kwargs):
    """Test a skill by running a prompt through a temporary quest."""
    skill = env["ai.skill"].browse(int(skill_id))
    if not skill.exists():
        return f"Error: Skill #{skill_id} not found"
    quest = env["ai.quest"].create({
        "name": f"Test: {skill.name}",
        "description": skill.recipe_text or skill.description or "",
        "init_type": "manual",
        "status": "draft",
    })
    try:
        result = quest.run(prompt=prompt)
        return result if result else "(empty response)"
    finally:
        quest.unlink()


def _tool_builder_create_skill(env, name, recipe_text, category="general",
                                trigger_keywords="", description="", **kwargs):
    """Save a skill permanently."""
    desc = description or (recipe_text[:200] if recipe_text else name)
    desc = desc[:1024]  # Max 1024 chars
    skill = env["ai.skill"].create({
        "name": name,
        "description": desc,
        "recipe_text": recipe_text,
        "category": category,
        "trigger_keywords": trigger_keywords,
        "compatibility": "any",
        "source_type": "odoo",
        "active": True,
    })
    return f"Skill #{skill.id} '{skill.name}' saved permanently (category: {category})"


def _tool_builder_improve_skill(env, skill_id, feedback, field="recipe_text", **kwargs):
    """Update a skill based on user feedback."""
    skill = env["ai.skill"].browse(int(skill_id))
    if not skill.exists():
        return f"Error: Skill #{skill_id} not found"
    valid_fields = ["recipe_text", "trigger_keywords", "description", "category"]
    if field not in valid_fields:
        return f"Error: Invalid field '{field}'. Valid: {valid_fields}"
    # For recipe_text, prepend improvement note and apply feedback
    if field == "recipe_text":
        current = skill.recipe_text or ""
        improved = f"{current}\n\n<!-- Improvement based on feedback: {feedback[:200]} -->\n"
        skill.write({field: improved})
        return f"Skill #{skill_id} recipe_text updated with improvement note. Review and refine manually."
    elif field == "trigger_keywords":
        current = skill.trigger_keywords or ""
        # Add new keywords if not already present
        new_kw = [k.strip() for k in feedback.split(",") if k.strip()]
        existing = set(k.strip().lower() for k in current.split(",") if k.strip())
        added = [k for k in new_kw if k.lower() not in existing]
        if added:
            updated = current + (", " if current else "") + ", ".join(added)
            skill.write({field: updated})
            return f"Skill #{skill_id} trigger_keywords updated: added {added}"
        return f"Skill #{skill_id}: no new keywords to add"
    else:
        skill.write({field: feedback[:1024]})
        return f"Skill #{skill_id} {field} updated"


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
