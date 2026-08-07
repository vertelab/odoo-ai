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
from typing import Any, Callable, Awaitable, List, Dict, Optional

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
    handler: Optional[Callable[..., Awaitable[str]]] = None
    risk_level: str = "read_only"  # safe | read_only | write | destructive | execute
    source: str = "custom"  # odoo_model | mcp | custom

    # Executor routing (tool-executor-nats)
    executor: str = "local"  # "local" | "nats" | "mcp"
    capabilities: List[str] = field(default_factory=list)  # e.g. ["browser", "web", "infra"]
    nats_subject: str = "pi.task.do"
    nats_skills: str = ""
    nats_timeout: int = 30

    # Access-grupper (ai-tool-access-capabilities): Odoo group ids som får
    # använda verktyget. Tom = obegränsat. PermissionEngine nekar anrop när
    # användarens grupper inte korsar dessa (defense-in-depth).
    group_ids: List[int] = field(default_factory=list)

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
        """Execute the tool with given parameters. Returns result as string.

        For NATS-executor tools (handler=None), returns an error —
        actual execution happens via _execute_via_nats in AgentLoop.
        """
        if self.handler is None:
            return f"Tool '{self.name}' requires remote executor ({self.executor})"
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
    """Search the web. Uses DuckDuckGo (DDGS); fallback till HTML-scrape
    om cert-laddning misslyckas (odoo-användaren saknar läsrätt till
    /usr/local/share/ca-certificates/mycacert.crt)."""
    if not query or not query.strip():
        return "Error: query is required"
    # 1. DDGS (primärt)
    try:
        from duckduckgo_search import DDGS
        results = list(DDGS().text(query, max_results=5))
        if results:
            return "\n".join(
                f"{i+1}. {r.get('title','?')}\n   {r.get('body','')[:200]}\n   {r.get('href','')}"
                for i, r in enumerate(results)
            )
    except ImportError:
        pass
    except Exception:
        pass
    # 2. HTML-scrape (fallback — kringgår cert-problemet)
    try:
        import httpx, html as html_mod
        from urllib.parse import quote_plus
        url = 'https://html.duckduckgo.com/html/?q=' + quote_plus(query)
        async with httpx.AsyncClient(timeout=15, verify=False) as client:
            r = await client.get(url, headers={
                'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64; rv:126.0) Gecko/20100101 Firefox/126.0'})
            r.raise_for_status()
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(r.text, 'html.parser')
        results = []
        for res in soup.select('.result')[:5]:
            a = res.select_one('.result__a')
            s = res.select_one('.result__snippet')
            if a:
                title = html_mod.unescape(a.get_text(strip=True))
                href = a.get('href', '')
                snippet = html_mod.unescape(
                    s.get_text(strip=True)) if s else ''
                results.append(f"{len(results)+1}. {title}\n   {snippet[:200]}\n   {href}")
        if results:
            return "\n".join(results)
        return "No results found."
    except Exception as e:
        return f"Search error: {e}"


async def _tool_fetch_url(url: str = "") -> str:
    """Fetch and extract text content from a URL."""
    if not url:
        return "Error: url is required"
    try:
        import httpx
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.get(url, headers={'User-Agent': 'Odoo-AI/1.0'})
        except Exception:
            # Cert-fallback (odoo-användaren kan inte läsa mycacert.crt)
            async with httpx.AsyncClient(timeout=15, verify=False) as client:
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


def _sanitize_tool_name(name: str) -> str:
    """Transliterera icke-ASCII-tecken i verktygsnamn.

    Cerebras/Bifrost kan inte kompilera JSON-schema-grammatik för verktyg
    med icke-ASCII-namn (t.ex. 'driftlarm_update_bedömning' → 400
    'Failed to compile the JSON schema grammar').
    """
    import unicodedata
    if name.isascii():
        return name
    normalized = unicodedata.normalize('NFKD', name)
    ascii_name = ''.join(
        c for c in normalized if not unicodedata.combining(c))
    return ascii_name


def ai_tool_records_to_tools(records, env=None) -> list[Tool]:
    """Convert ai.tool records to executable core Tool objects.

    Change ai-orchestration-tidy-up 7.5: buzz-agenter ska kunna köra med
    EGNA tools (via identity_id.tool_ids / ai.tool) istället för att bara
    ärva coworkerns hela verktygssats. Varje tool exekveras genom
    ai.tool._execute_tool() (sandboxad Python-kod).
    """
    import json as _json
    tools = []
    for record in records:
        # Builtin-verktyg (explicit-agent-tools): returnera den riktiga
        # builtin Tool med dess Python-handler. group_ids från posten
        # behålls så access-kontroll fungerar som för custom-verktyg.
        if record.builtin_name:
            bt = next(
                (t for t in builtin_tools()
                 if t.name == record.builtin_name), None)
            if bt:
                import dataclasses
                tools.append(dataclasses.replace(
                    bt, group_ids=list(record.group_ids.ids)
                    if record.group_ids else []))
                continue
        try:
            params = _json.loads(record.parameters or '{}') or {}
        except Exception:
            params = {}
        schema = {
            'type': 'object',
            'properties': params.get('properties', {}) if isinstance(params, dict) else {},
            'required': params.get('required', []) if isinstance(params, dict) else [],
        }

        async def _handler(_rec=record, **kwargs):
            try:
                return _rec._execute_tool(kwargs)
            except Exception as e:
                return f'Tool {_rec.name} failed: {e}'

        tools.append(Tool(
            name=_sanitize_tool_name(record.name),
            description=record.description or record.name,
            parameters=schema,
            handler=_handler,
            risk_level=record.risk_level or 'read_only',
            executor=record.executor or 'local',
            capabilities=(
                [c.strip() for c in record.capabilities.split(',') if c.strip()]
                if record.capabilities else []),
            group_ids=list(record.group_ids.ids) if record.group_ids else [],
            nats_subject=record.nats_subject or 'pi.task.do',
            nats_skills=record.nats_skills or '',
            nats_timeout=record.nats_timeout or 30,
        ))
    return tools


# ---------------------------------------------------------------------------
# Capability-serialisering (ai-tool-access-capabilities)
# ---------------------------------------------------------------------------
# En förmåga (ai.tool.capability) = namn + AI-beskrivning + medlemmar.
# Access (group_ids) filtreras INNAN dessa funktioner anropas (spec 3.5) —
# medlemmarna i registryn är redan tillåtna för användaren.
# ---------------------------------------------------------------------------

# Max operationer per enum-tool (spec: 5–8; fler → delas eller namespace).
CAPABILITY_ENUM_MAX_OPS = 8


def _capability_enum_schema(members):
    """Bygg JSON-schema för ett enum-tool: operation + alla medlemmars
    parametrar (best-effort-merge; required hålls till operation — villkorade
    parametrar beskrivs i text, inte i schema, för små modellers skull)."""
    properties = {
        'operation': {
            'type': 'string',
            'enum': [m.name for m in members],
            'description': 'Operation att utföra. Välj exakt en av enumen.',
        },
    }
    for m in members:
        for k, v in (m.parameters or {}).get('properties', {}).items():
            properties.setdefault(k, v)
    return {
        'type': 'object',
        'properties': properties,
        'required': ['operation'],
    }


def _capability_enum_description(cap_name, cap_desc, members):
    """Samlad AI-beskrivning med per-operation-rader (guardrails informativa)."""
    lines = [f"{cap_name}: {cap_desc or ''}".strip(), '', 'Operationer:']
    for m in members:
        op_desc = (m.description or m.name).replace('\n', ' ')
        lines.append(f'- {m.name}: {op_desc}')
    return '\n'.join(lines)


def capability_enum_tool(cap_name, cap_desc, members):
    """Bygg EN Tool per förmåga med operation-enum (spec 3.3).

    members: list[Tool] — redan access-filtrerade medlemmar.
    Risk = max av medlemmarnas risk (destruktiv medlem → HITL på hela).
    group_ids lämnas tomma: verktyget existerar bara efter access-filtrering.
    """
    if len(members) > CAPABILITY_ENUM_MAX_OPS:
        raise ValueError(
            f"Capability '{cap_name}' has {len(members)} operations "
            f"> {CAPABILITY_ENUM_MAX_OPS} — dela förmågan eller använd "
            'namespace-läge (spec tool-capability-serialization).')

    by_name = {m.name: m for m in members}
    max_risk = max(
        (Tool.RISK_LEVELS.get(m.risk_level, 1) for m in members),
        default=1)
    risk_level = next(
        (r for r, lvl in sorted(
            Tool.RISK_LEVELS.items(), key=lambda kv: kv[1], reverse=True)
         if lvl <= max_risk), 'read_only')

    async def _handler(_by_name=by_name, **kwargs):
        op = kwargs.pop('operation', None)
        member = _by_name.get(op)
        if member is None:
            return (f"Error: unknown operation '{op}' for capability "
                    f"'{cap_name}'")
        try:
            return await member.execute(**kwargs)
        except Exception as e:
            return f"Tool error ({cap_name}/{op}): {e}"

    return Tool(
        name=cap_name,
        description=_capability_enum_description(cap_name, cap_desc, members),
        parameters=_capability_enum_schema(members),
        handler=_handler,
        risk_level=risk_level,
        source='capability',
        executor='local',
        capabilities=['capability'],
    )


def capability_namespace_prompt(capabilities):
    """Systemprompt-suffix för namespace-läge (spec 3.4): individuella verktyg
    behålls, förmågans samlade beskrivning injiceras för att styra valet.

    capabilities: list[dict] med name/description/member_names.
    """
    if not capabilities:
        return ''
    lines = ['', '## Förmågor (capabilities)', '']
    for cap in capabilities:
        lines.append(f'### {cap["name"]}')
        lines.append(cap.get('description') or '')
        members = cap.get('member_names') or []
        if members:
            lines.append('Medlemmar: ' + ', '.join(members))
        lines.append('')
    return '\n'.join(lines).rstrip()


def apply_capability_serialization(registry, capabilities, mode):
    """Applicera förmågeserialisering på ett ToolRegistry (spec 3.3/3.4).

    Args:
        registry: ToolRegistry med redan access-filtrerade verktyg.
        capabilities: list[dict] med name/description/member_names —
                      member_names är verktygsnamn i registryn.
        mode: 'flat' (no-op) | 'enum' | 'namespace'.

    Returns:
        prompt_suffix: str — namespace-prompt att lägga i systemprompten
                       (tom för flat/enum).
    """
    if mode == 'flat' or not capabilities:
        return ''

    if mode == 'namespace':
        return capability_namespace_prompt(capabilities)

    if mode == 'enum':
        for cap in capabilities:
            members = [
                registry.get(n) for n in cap.get('member_names') or []
                if registry.get(n) is not None]
            if not members:
                continue
            if len(members) > CAPABILITY_ENUM_MAX_OPS:
                # Dela i ≤8-operationers enheter (spec 3.3 scenario).
                for i in range(0, len(members), CAPABILITY_ENUM_MAX_OPS):
                    chunk = members[i:i + CAPABILITY_ENUM_MAX_OPS]
                    name = f"{cap['name']}_{i // CAPABILITY_ENUM_MAX_OPS + 1}"
                    registry.register(capability_enum_tool(
                        name, cap.get('description', ''), chunk))
            else:
                registry.register(capability_enum_tool(
                    cap['name'], cap.get('description', ''), members))
            # Ta bort individuella medlemmar — LLM:en ser bara enheterna.
            for m in members:
                registry._tools.pop(m.name, None)
        return ''

    return ''


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
            name="browser_navigate",
            description="Navigate to a URL using a headless browser and return the full page text content. "
                        "Useful for browsing websites that require JavaScript rendering, "
                        "checking if sites are up, or extracting content from dynamic pages. "
                        "Delegates to a Pi-agent via NATS.",
            parameters={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The URL to navigate to (e.g. https://example.com)",
                    },
                    "action": {
                        "type": "string",
                        "enum": ["snapshot", "screenshot", "check_uptime"],
                        "description": "What to do on the page. snapshot = read page text, "
                                       "screenshot = capture image, check_uptime = verify page loads",
                    },
                },
                "required": ["url"],
            },
            handler=None,
            risk_level="read_only",
            source="builtin",
            executor="nats",
            capabilities=["browser"],
            nats_subject="pi.task.do",
            nats_skills="agent-browser",
        ),
        Tool(
            name="graph_query",
            description="Query the Odoo Mind graph database using Cypher. "
                        "Use this to find relationships between Odoo records — "
                        "partners, companies, emails, invoices, strategy plans, "
                        "knowledge articles. "
                        "Returns JSON with nodes and relationships. "
                        "Example: MATCH (p:OdooPartner {id: 42})-[:HAS_CONTACT]->(person) RETURN person.name, person.email "
                        "READ-ONLY only. Cannot modify data.",
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Cypher query. Must be read-only (MATCH/RETURN only). "
                                       "Example: MATCH (p:OdooPartner)-[:BELONGS_TO]->(c:Company {id: 1}) RETURN p.name",
                    },
                },
                "required": ["query"],
            },
            handler=_tool_graph_query,
            risk_level="read_only",
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
            description="List all active ai.coworker records with name, status, and init_type.",
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
            description="Create a new ai.coworker record. Params: name (required), description, init_types (list), is_supervisor (bool). Returns quest ID.",
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
            description="Update an existing ai.coworker. Params: coworker_id (required), then any of: name, description, is_supervisor.",
            parameters={"type": "object", "properties": {
                "coworker_id": {"type": "integer", "description": "Quest ID to update"},
                "name": {"type": "string"},
                "description": {"type": "string"},
                "is_supervisor": {"type": "boolean"},
            }, "required": ["coworker_id"]},
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
            description="Assign an agent to a quest. Params: coworker_id, agent_id, sequence (optional).",
            parameters={"type": "object", "properties": {
                "coworker_id": {"type": "integer"},
                "agent_id": {"type": "integer"},
                "sequence": {"type": "integer", "description": "Order in the agent pipeline (1-based)"},
            }, "required": ["coworker_id", "agent_id"]},
            handler=_tool_builder_assign_agent,
            risk_level="destructive",
            source="builtin",
        ),
        Tool(
            name="builder_configure_init_type",
            description="Configure an init type on a quest. Params: coworker_id, init_type (one of: web_ui,chat,channel,mail,cron,server_action,powerbox,manual,openai_api), config_json (type-specific config).",
            parameters={"type": "object", "properties": {
                "coworker_id": {"type": "integer"},
                "init_type": {"type": "string", "description": "Init type to configure"},
                "config": {"type": "string", "description": "JSON config: for powerbox use {\"model_ids\": [\"sale.order\",\"crm.lead\"]}, for mail use {\"alias_name\":\"support\"}"},
            }, "required": ["coworker_id", "init_type"]},
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

        # ── Generic Odoo Model Tools (odoo-model-tools change) ──
        Tool(
            name="describe_model",
            description="Return a model's schema: fields (type/readonly/computed), relations, available action_*/button_* methods, and capabilities (has_okf, has_graph, has_embedding). Use before searching or writing to a model.",
            parameters={
                "type": "object",
                "properties": {
                    "model": {"type": "string", "description": "Odoo model technical name, e.g. crm.lead"},
                },
                "required": ["model"],
            },
            handler=_tool_describe_model,
            risk_level="read_only",
            source="odoo_model",
        ),
        Tool(
            name="odoo_search",
            description="Search records via the ORM (search_read) with Odoo domain syntax. Returns id/name/display_name/create_date by default; pass fields to get more. Limit defaults to 20.",
            parameters={
                "type": "object",
                "properties": {
                    "model": {"type": "string"},
                    "domain": {"type": "array", "description": "Odoo search domain syntax, e.g. [[\x27state\x27, \x27=\x27, \x27draft\x27]]", "items": {"type": "array"}},
                    "fields": {"type": "array", "items": {"type": "string"}},
                    "limit": {"type": "integer", "default": 20},
                    "offset": {"type": "integer"},
                    "order": {"type": "string"},
                },
                "required": ["model"],
            },
            handler=_tool_odoo_search,
            risk_level="read_only",
            source="odoo_model",
        ),
        Tool(
            name="odoo_create",
            description="Create a record via the ORM (affärslager) — defaults/onchange tillämpas. Returns id and display_name. Requires approval.",
            parameters={
                "type": "object",
                "properties": {
                    "model": {"type": "string"},
                    "values": {"type": "object", "description": "Field values"},
                },
                "required": ["model", "values"],
            },
            handler=_tool_odoo_create,
            risk_level="write",
            source="odoo_model",
        ),
        Tool(
            name="odoo_call_method",
            description="Call a business method (action_*/button_*) on a record — e.g. action_confirm, action_post. Use instead of writing state directly. Requires approval (HITL) always.",
            parameters={
                "type": "object",
                "properties": {
                    "model": {"type": "string"},
                    "id": {"type": "integer"},
                    "method": {"type": "string", "description": "Method name, must start with action_ or button_ (or be whitelisted)"},
                    "args": {"type": "object", "description": "Optional keyword arguments"},
                },
                "required": ["model", "id", "method"],
            },
            handler=_tool_odoo_call_method,
            risk_level="execute",
            source="odoo_model",
        ),
        Tool(
            name="odoo_write",
            description="Update safe fields on records. Never write state/move_type/journal_id/amount_* directly — use odoo_call_method for business flows. Requires approval.",
            parameters={
                "type": "object",
                "properties": {
                    "model": {"type": "string"},
                    "ids": {"type": "array", "items": {"type": "integer"}},
                    "values": {"type": "object"},
                },
                "required": ["model", "ids", "values"],
            },
            handler=_tool_odoo_write,
            risk_level="write",
            source="odoo_model",
        ),
        Tool(
            name="odoo_unlink",
            description="Delete records. USE WITH CAUTION — destructive, always requires human approval (hard stop).",
            parameters={
                "type": "object",
                "properties": {
                    "model": {"type": "string"},
                    "ids": {"type": "array", "items": {"type": "integer"}},
                },
                "required": ["model", "ids"],
            },
            handler=_tool_odoo_unlink,
            risk_level="destructive",
            source="odoo_model",
        ),
        Tool(
            name="okf_search",
            description="Search OKF knowledge concepts (ai.okf.concept) — hybrid vector + full-text search with access filtering. Use for knowledge about processes/context when OKF exists.",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "scope": {"type": "string", "enum": ["company", "personal", "coworker"]},
                    "limit": {"type": "integer", "default": 10},
                },
                "required": ["query"],
            },
            handler=_tool_okf_search,
            risk_level="read_only",
            source="odoo_model",
        ),
    ]


async def _tool_echo(message: str) -> str:
    """Echo back the message. Test tool."""
    return message


async def _tool_graph_query(env, query: str = "") -> str:
    """Query the Odoo Mind AGE graph using Cypher.

    Runs read-only Cypher queries against the odoo_mind graph.
    Returns results as JSON string.

    Args:
        env: Odoo environment (injected by wrap_tools_with_env)
        query: Cypher query string (MATCH/RETURN only)

    Returns:
        JSON string with query results
    """
    import json
    if not query or not query.strip():
        return json.dumps({"error": "query is required"})
    try:
        executor = env['graph.executor']
        if not executor.is_age_available():
            return json.dumps({
                "error": "Odoo Mind graph is not available. "
                         "Apache AGE extension may not be installed.",
                "hint": "Run: salt '*' state.apply postgres.age",
            })
        results = executor.cypher(query.strip(), read_only=True, timeout=10)
        return json.dumps(results, indent=2, default=str)
    except Exception as e:
        return json.dumps({"error": f"Graph query failed: {e}"})


# Global NATS connection (lazy-init)
_nats_connection = None


# ---------------------------------------------------------------------------
# NATS Executor — request-reply for tool delegation (tool-executor-nats)
# ---------------------------------------------------------------------------


async def _ensure_nats_connection(timeout: float = 5.0):
    """Get or create the NATS connection. Returns (connection, error_string)."""
    global _nats_connection
    import os
    import asyncio

    if _nats_connection is not None and not _nats_connection.is_closed:
        return _nats_connection, None

    _nats_connection = None  # Reset dead connection
    try:
        from nats import connect as nats_connect
        nats_url = os.environ.get('NATS_URL', 'nats://localhost:4222')
        _nats_connection = await asyncio.wait_for(
            nats_connect(nats_url), timeout=timeout
        )
        return _nats_connection, None
    except asyncio.TimeoutError:
        return None, f'NATS connection timeout to {nats_url}'
    except Exception as e:
        return None, f'NATS connection failed: {e}'


async def nats_request_reply(
    subject: str,
    payload: dict,
    timeout: float = 30.0,
    conn=None,
) -> str:
    """Send a NATS request and wait for reply.

    Uses NATS Core request-reply with _INBOX routing.
    The reply subject is auto-generated by NATS — only this connection
    can receive replies to this request (MITM-safe at transport level).

    Args:
        subject: NATS subject to send request to
        payload: Dictionary to serialize as JSON payload
        timeout: Max seconds to wait for reply
        conn: Existing NATS connection, or None to lazy-init

    Returns:
        Reply data as decoded string, or error message string
    """
    import json as json_mod

    # Get connection
    if conn is None:
        conn, err = await _ensure_nats_connection(timeout=min(timeout, 10.0))
        if err:
            return err

    import asyncio
    try:
        data = json_mod.dumps(payload).encode()
        response = await asyncio.wait_for(
            conn.request(subject, data, timeout=timeout),
            timeout=timeout + 1.0,  # Slight buffer for NATS timeout propagation
        )
        reply = response.data.decode()
        _logger.info("NATS request-reply: subject=%s, payload=%d bytes, reply=%d bytes",
                     subject, len(data), len(reply))
        return reply
    except asyncio.TimeoutError:
        return f'NATS request timed out after {timeout}s on subject \'{subject}\''
    except Exception as e:
        _logger.warning("NATS request-reply failed: %s", e)
        return f'NATS request failed: {e}'


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


# ---------------------------------------------------------------------------
# Generic Odoo Model Tools (odoo-model-tools change)
# Ersätter den döda per-modell-generatorn (model_to_tools). Sex generiska
# verktyg med modellen som parameter — kontextfönstret exploderar inte med
# tusentals verktyg. Handlers tar `env` som första arg, injiceras av
# wrap_tools_with_env.
# ---------------------------------------------------------------------------

# Fält som aldrig får skrivas direkt — affärsflöden går via metoder
_ODOO_WRITE_DENYLIST = (
    'state', 'move_type', 'journal_id', 'amount_',
)


def _model_scope_error(env, model):
    """Return error-sträng om modellen ligger utanför scopen (annars '')."""
    scoped = env.context.get('_ai_scoped_models')
    if scoped and model not in scoped:
        return ('Model %s is not in the scoped models for this init type '
                '(scoped: %s)' % (model, sorted(scoped)))
    return ''


def _tool_describe_model(env, model=''):
    """Return model schema + capabilities (fält, relationer, action-metoder,
    has_okf/has_graph/has_embedding)."""
    import json as _json
    if not model or model not in env.registry:
        return _json.dumps({"error": f"Unknown model: {model}"})
    _scope_err = _model_scope_error(env, model)
    if _scope_err:
        return _json.dumps({"error": _scope_err})
    Model = env[model]
    try:
        fields_get = Model.fields_get()
    except Exception as e:
        return _json.dumps({"error": f"fields_get failed for {model}: {e}"})

    fields = {}
    for fname, finfo in fields_get.items():
        field_obj = Model._fields.get(fname)
        fields[fname] = {
            'type': finfo.get('type'),
            'string': finfo.get('string'),
            'readonly': bool(finfo.get('readonly')),
            'required': bool(finfo.get('required')),
            'relation': finfo.get('relation'),
            'computed': bool(getattr(field_obj, 'compute', False)),
            'related': bool(getattr(field_obj, 'related', False)),
        }
    relations = {
        fname: finfo.get('relation')
        for fname, finfo in fields_get.items()
        if finfo.get('type') in ('many2one', 'one2many', 'many2many')
        and finfo.get('relation')
    }
    action_methods = sorted({
        name for name in dir(Model)
        if (name.startswith('action_') or name.startswith('button_'))
        and not name.startswith('__')
        and callable(getattr(Model, name, None))
    })
    has_okf = has_graph = has_embedding = False
    try:
        if 'ai.artifact.type' in env:
            has_okf = env['ai.artifact.type'].search_count(
                [('model_id.model', '=', model)]) > 0
    except Exception:
        pass
    try:
        if 'graph.node.definition' in env:
            has_graph = env['graph.node.definition'].search_count(
                [('model_id.model', '=', model)]) > 0
    except Exception:
        pass
    try:
        for field_obj in Model._fields.values():
            ctype = getattr(field_obj, 'column_type', (None,))[0]
            if ctype == 'vector' or getattr(field_obj, 'type', '') == 'vector':
                has_embedding = True
                break
    except Exception:
        pass

    return _json.dumps({
        'model': model,
        'fields': fields,
        'relations': relations,
        'action_methods': action_methods,
        'capabilities': {
            'has_okf': has_okf,
            'has_graph': has_graph,
            'has_embedding': has_embedding,
        },
    }, default=str)


def _tool_odoo_search(env, model='', domain=None, fields=None, limit=20,
                      offset=0, order=None):
    """Search records via ORM (search_read). Defaultfält id/name/
    display_name/create_date, limit 10–20. html/text/binary exkluderas i
    default men respekteras om explicit begärda."""
    import json as _json
    if not model or model not in env.registry:
        return _json.dumps({"error": f"Unknown model: {model}"})
    _scope_err = _model_scope_error(env, model)
    if _scope_err:
        return _json.dumps({"error": _scope_err})
    Model = env[model]
    default_fields = fields is None
    if fields is None:
        fields = ['id']
        for fname in ('name', 'display_name', 'create_date'):
            if fname in Model._fields:
                fields.append(fname)
    elif 'id' not in fields:
        fields = ['id'] + list(fields)
    if default_fields:
        try:
            fg = Model.fields_get(fields)
        except Exception:
            fg = {}
        fields = [f for f in fields
                  if fg.get(f, {}).get('type') not in ('html', 'text', 'binary')]
    try:
        records = Model.search_read(
            domain or [], fields=fields, limit=limit or 20,
            offset=offset or 0, order=order)
    except Exception as e:
        return _json.dumps({"error": f"search_read failed on {model}: {e}"})
    return _json.dumps(records, default=str)


def _tool_odoo_create(env, model='', values=None):
    """Create a record via ORM (affärslager). Returnerar id + display_name."""
    import json as _json
    if not model or model not in env.registry:
        return _json.dumps({"error": f"Unknown model: {model}"})
    _scope_err = _model_scope_error(env, model)
    if _scope_err:
        return _json.dumps({"error": _scope_err})
    Model = env[model]
    if not Model.check_access_rights('create', raise_exception=False):
        return _json.dumps({"error": f"No create access on {model}"})
    try:
        rec = Model.create(dict(values or {}))
    except Exception as e:
        return _json.dumps({"error": f"create failed on {model}: {e}"})
    return _json.dumps({
        "ok": True, "id": rec.id,
        "name": rec.display_name or rec.name or '',
    }, default=str)


def _tool_odoo_call_method(env, model='', id=None, method='', args=None):
    """Anropa affärsmetod (action_*/button_* eller vitlista). HITL alltid."""
    import json as _json
    if not model or model not in env.registry:
        return _json.dumps({"error": f"Unknown model: {model}"})
    _scope_err = _model_scope_error(env, model)
    if _scope_err:
        return _json.dumps({"error": _scope_err})
    if not method or method.startswith('_'):
        return _json.dumps({"error": f"Method '{method}' is not allowed"})
    if not (method.startswith('action_') or method.startswith('button_')):
        try:
            whitelist = env['ir.config_parameter'].get_param(
                'ai_agent_core.call_method_whitelist', '') or ''
            allowed = {m.strip() for m in whitelist.split(',') if m.strip()}
        except Exception:
            allowed = set()
        if method not in allowed:
            return _json.dumps({
                "error": f"Method '{method}' not allowed "
                         f"(endast action_*/button_* eller vitlista)"})
    Model = env[model]
    rec = Model.browse(id)
    if not rec.exists():
        return _json.dumps({"error": f"{model} {id} not found"})
    if not hasattr(rec, method) or not callable(getattr(rec, method)):
        return _json.dumps({"error": f"Method '{method}' not found on {model}"})
    try:
        result = getattr(rec, method)(**(args or {}))
    except Exception as e:
        return _json.dumps({
            "error": f"Method call {model}.{method} failed: {e}"})
    return _json.dumps({"ok": True, "result": str(result)}, default=str)


def _tool_odoo_write(env, model='', ids=None, values=None):
    """Skriv säkra fält (skrivbara, icke-computed/related, utanför denylist).
    Skriv aldrig state direkt — affärsflöden via odoo_call_method."""
    import json as _json
    if not model or model not in env.registry:
        return _json.dumps({"error": f"Unknown model: {model}"})
    _scope_err = _model_scope_error(env, model)
    if _scope_err:
        return _json.dumps({"error": _scope_err})
    Model = env[model]
    vals = dict(values or {})
    if not vals:
        return _json.dumps({"error": "values krävs"})
    try:
        fg = Model.fields_get(list(vals.keys()))
    except Exception:
        fg = {}
    allowed = {}
    rejected = []
    for fname, fval in vals.items():
        if fname in _ODOO_WRITE_DENYLIST or any(
                fname.startswith(d) for d in _ODOO_WRITE_DENYLIST):
            rejected.append(fname)
            continue
        finfo = fg.get(fname, {})
        field_obj = Model._fields.get(fname)
        if finfo.get('readonly') or getattr(field_obj, 'compute', False) \
                or getattr(field_obj, 'related', False):
            rejected.append(fname)
            continue
        allowed[fname] = fval
    if rejected:
        return _json.dumps({
            "error": f"Icke tillåtna fält: {rejected}",
            "hint": "Använd odoo_call_method för affärsflöden "
                    "(state ändras via metoder)"})
    recs = Model.browse(ids or [])
    if not recs:
        return _json.dumps({"error": f"Inga {model}-poster med ids={ids}"})
    try:
        recs.write(allowed)
    except Exception as e:
        return _json.dumps({"error": f"write failed on {model}: {e}"})
    return _json.dumps({"ok": True, "ids": list(recs.ids)})


def _tool_odoo_unlink(env, model='', ids=None):
    """Radera poster. EXTERNAL-risk — HITL + hårt stopp i permission engine."""
    import json as _json
    if not model or model not in env.registry:
        return _json.dumps({"error": f"Unknown model: {model}"})
    _scope_err = _model_scope_error(env, model)
    if _scope_err:
        return _json.dumps({"error": _scope_err})
    recs = env[model].browse(ids or [])
    if not recs:
        return _json.dumps({"error": f"Inga {model}-poster med ids={ids}"})
    try:
        recs.unlink()
    except Exception as e:
        return _json.dumps({"error": f"unlink failed on {model}: {e}"})
    return _json.dumps({"ok": True, "deleted": list(recs.ids)})


def _tool_okf_search(env, query='', scope='company', limit=10):
    """Sök OKF-koncept via _okf_search (hybrid pgvector + tsvector + access)."""
    import json as _json
    if not query:
        return _json.dumps({"error": "query krävs"})
    if 'ai.okf.concept' not in env:
        return _json.dumps({"error": "OKF inte tillgängligt"})
    try:
        kw = {'query': query, 'limit': limit or 10}
        if scope in ('company', 'personal', 'coworker'):
            kw['scope'] = scope
        results = env['ai.okf.concept']._okf_search(**kw)
        out = [{
            'id': c.id, 'title': c.title,
            'summary': (c.summary or '')[:500],
            'concept_key': c.concept_key, 'scope': c.scope, 'status': c.status,
        } for c in results]
        return _json.dumps(out, default=str)
    except Exception as e:
        return _json.dumps({"error": f"okf_search failed: {e}"})
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
    if hasattr(env["ai.coworker.init_type"], "INIT_TYPE_SELECTION"):
        result["init_types"] = [
            {"key": k, "label": v}
            for k, v in env["ai.coworker.init_type"].INIT_TYPE_SELECTION
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
    quests = env["ai.coworker"].search([("active", "=", True)])
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
        model_name = a.model_id.name if a.model_id else "none"
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
    """Create a new ai.coworker. Returns quest ID."""
    vals = {
        "name": name,
        "description": description,
        "status": "active",
        "is_supervisor": bool(is_supervisor),
    }
    quest = env["ai.coworker"].create(vals)

    # Configure init types
    if init_types:
        for itype in [t.strip() for t in init_types.split(",") if t.strip()]:
            env["ai.coworker.init_type"].create({
                "coworker_id": coworker.id,
                "init_type": itype,
                "active": True,
            })

    return f"Quest #{coworker.id} '{quest.name}' created with init_types: {init_types or 'none'}"


def _tool_builder_update_quest(env, coworker_id, **kwargs):
    """Update an existing ai.coworker. Only updates provided fields."""
    quest = env["ai.coworker"].browse(int(coworker_id))
    if not quest.exists():
        return f"Error: Quest #{coworker_id} not found"
    updates = {}
    for field in ("name", "description"):
        if field in kwargs and kwargs[field]:
            updates[field] = kwargs[field]
    if "is_supervisor" in kwargs:
        updates["is_supervisor"] = bool(kwargs["is_supervisor"])
    if updates:
        quest.write(updates)
    return f"Quest #{coworker.id} updated: {list(updates.keys())}"


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


def _tool_builder_assign_agent(env, coworker_id, agent_id, sequence=10, **kwargs):
    """Assign an agent to a quest."""
    quest = env["ai.coworker"].browse(int(coworker_id))
    agent = env["ai.agent"].browse(int(agent_id))
    if not quest.exists():
        return f"Error: Quest #{coworker_id} not found"
    if not agent.exists():
        return f"Error: Agent #{agent_id} not found"
    existing = env["ai.coworker.agent"].search([
        ("coworker_id", "=", coworker.id),
        ("agent_id", "=", agent.id),
    ])
    if existing:
        return f"Agent '{agent.name}' already assigned to quest '{quest.name}'"
    env["ai.coworker.agent"].create({
        "coworker_id": coworker.id,
        "agent_id": agent.id,
        "sequence": int(sequence),
    })
    return f"Agent '{agent.name}' assigned to quest '{quest.name}' (sequence: {sequence})"


def _tool_builder_configure_init_type(env, coworker_id, init_type, config="{}", **kwargs):
    """Configure an init type on a quest."""
    import json as _json
    quest = env["ai.coworker"].browse(int(coworker_id))
    if not quest.exists():
        return f"Error: Quest #{coworker_id} not found"
    config_data = _json.loads(config) if isinstance(config, str) else config
    vals = {
        "coworker_id": coworker.id,
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
    init_record = env["ai.coworker.init_type"].create(vals)
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
    quest = env["ai.coworker"].create({
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


# ---------------------------------------------------------------------------
# env-injection wrapper (builder tools need Odoo env as first arg)
# ---------------------------------------------------------------------------

def wrap_tools_with_env(tools, env):
    """Wrap tool handlers so they receive the Odoo env and are awaitable.

    Builtin handlers are declared as sync ``handler(env, **kwargs)``, but
    ``Tool.execute`` calls ``await self.handler(**kwargs)`` — without this
    wrapper the call fails twice over (missing env, awaiting a sync fn).

    Handlers whose signature has no ``env`` parameter are only wrapped for
    sync→async safety. Async handlers and NATS tools (handler=None) pass
    through unchanged.
    """
    import dataclasses
    import functools
    import inspect

    wrapped = []
    for tool in tools:
        handler = tool.handler
        if handler is None:
            wrapped.append(tool)
            continue

        sig = inspect.signature(handler)
        needs_env = 'env' in sig.parameters
        is_async = inspect.iscoroutinefunction(handler)

        if needs_env and is_async:
            @functools.wraps(handler)
            async def wh(_h=handler, **kwargs):
                return await _h(env, **kwargs)
        elif needs_env:
            @functools.wraps(handler)
            async def wh(_h=handler, **kwargs):
                return _h(env, **kwargs)
        elif not is_async:
            @functools.wraps(handler)
            async def wh(_h=handler, **kwargs):
                return _h(**kwargs)
        else:
            wh = handler

        wrapped.append(dataclasses.replace(tool, handler=wh))
    return wrapped


def specialist_tools(agents) -> list[Tool]:
    """Build a Tool per specialist agent for supervisor delegation.

    Each tool invokes the specialist's AgentLoop.run() with a query
    (and optional context). Async native — runs in the same event loop.

    Args:
        agents: list of (name, description, loop) tuples
                where loop has async run(prompt) -> ChatResponse

    Returns:
        list[Tool] ready for ToolRegistry
    """
    tools = []
    for name, description, loop in agents:
        safe_name = name.strip().lower().replace(' ', '_').replace('-', '_')
        tool = Tool(
            name=f"call_specialist_{safe_name}",
            description=(
                f"Delegera en uppgift till specialisten '{name}'. "
                f"{description or ''} Använd denna för frågor som hör till "
                f"denna specialists kompetens. Parametrar: query (uppgiften), "
                f"context (relevant bakgrund, valfritt)."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Precis uppgift att utföra."
                    },
                    "context": {
                        "type": "string",
                        "description": "Relevant bakgrund för uppgiften (valfritt)."
                    },
                },
                "required": ["query"],
            },
            risk_level="read_only",  # Delegation är i sig ingen skrivande handling
            source="specialist",
        )

        async def _handler(_loop=loop, **kwargs):
            query = kwargs.get("query", "")
            context = kwargs.get("context", "")
            prompt = f"{context}\n\n{query}" if context else query
            try:
                result = await _loop.run(prompt)
                text = result.text if hasattr(result, 'text') else str(result)
                return text[:8000] if text else "(tomt svar)"
            except Exception as e:
                return f"Specialist error: {e}"

        # Replace handler (dataclass frozen? — replace)
        from dataclasses import replace
        tools.append(replace(tool, handler=_handler))
    return tools
