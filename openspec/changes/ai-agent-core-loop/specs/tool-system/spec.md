# Spec: Tool System (without LangChain/LangGraph)

## Requirements

### TOOL-001 [NOW] — Tool abstraction (no LangChain)
The system MUST provide a tool system independent of LangChain.
- Tool definition: `{name, description, parameters: JSONSchema, risk_level, handler}`
- OpenAI function-calling format is the standard — no framework wrapper needed
- NO inheritance from LangChain `BaseTool` or `StructuredTool`
- Tool serialization MUST produce valid OpenAI `tools` array for provider API
- Tools are plain Python objects, not LangChain adapters

### TOOL-002 [NOW] — Tool types
The system MUST support three tool types:
- **OdooModelTools**: auto-generated from registered Odoo models
  - `search_read(model, domain, fields, limit)` — read records
  - `read(model, ids, fields)` — read specific records
  - `write(model, ids, values)` — write records
  - `create(model, values)` — create record
  - `unlink(model, ids)` — delete records
  - Each model becomes a tool with its own parameters
  - Respects Odoo access rights (ACL)
- **MCPTools**: discovered from MCP servers at session start
  - Tool definitions fetched via `tools/list` MCP method
  - Tool execution via `tools/call` MCP method
  - One MCP server = multiple tools
- **CustomTools**: user-defined via `ai.tool` Odoo model
  - Python code evaluated in sandboxed context
  - Parameters defined via JSON Schema
  - Can call Odoo ORM, external APIs

### TOOL-003 [NOW] — Tool registration
The system MUST register tools at session start.
- `ToolRegistry` aggregates tools from all sources
- Tools are addressable by name: `registry.get("search_read_sale_order")`
- Name collisions resolved by: explicit MCP prefix > Odoo model > custom
- Tools can be enabled/disabled per quest and per session
- Tool registration MUST be idempotent

### TOOL-004 [NOW] — Tool execution
The system MUST execute tools without LangChain involvement.
- `Tool.execute(**params)` → `str` (tool result as text)
- Tool execution MUST be bounded by `tool_timeout` (seconds)
- Failed tools MUST return error strings, not raise exceptions into the loop
- Tool results MUST be appended to message history as `ToolMessage`
- Large tool results MUST be truncated to `max_tool_result_bytes`
- Tool execution MUST use the authenticated user's Odoo environment

### TOOL-005 [NOW] — Tool serialization for providers
The system MUST serialize tools to provider-native formats.
- OpenAI format: `{"type": "function", "function": {"name": "...", "description": "...", "parameters": {...}}}`
- Anthropic format: `{"name": "...", "description": "...", "input_schema": {...}}`
- Serialization MUST be lazy (only when needed for API call)
- Provider-specific serializers in the provider layer

### TOOL-006 [NOW] — Tool risk levels
The system MUST assign risk levels to all tools.
- `safe`: read-only lookups, never require approval
- `read_only`: reads data, no side effects (default for search_read)
- `write`: modifies existing data (write, create)
- `destructive`: deletes data (unlink), always requires approval
- `execute`: runs arbitrary code (custom tools), always requires approval
- Risk level drives `InterruptHandler.approve_tool()` behavior

### TOOL-007 [NEXT] — Dynamic tool discovery
The system SHOULD support dynamic tool registration during a session.
- MCP servers can register new tools mid-session
- Tool list can be refreshed without restarting the loop
- Agent can request "what tools are available?"

### TOOL-008 [NEXT] — Tool documentation
The system SHOULD generate tool documentation from definitions.
- Each tool has a human-readable description
- Parameter documentation from JSON Schema
- Usage examples for common patterns
- Generated docs live in `recipes/tools/`

## Tool Definition Format

```python
@dataclass
class Tool:
    name: str                          # "search_read_sale_order"
    description: str                   # "Search and read sale orders"
    parameters: dict                   # JSON Schema for parameters
    risk_level: str                    # safe | read_only | write | destructive | execute
    handler: Callable                  # async def execute(**params) -> str
    source: str                        # "odoo_model" | "mcp" | "custom"
    
    def to_openai_tool(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            }
        }
    
    def to_anthropic_tool(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.parameters,
        }
```

## Non-requirements
- NOT wrapping tools in LangChain BaseTool or StructuredTool
- NOT supporting LangChain-specific tool features (callbacks, verbose, etc.)
- NOT building a visual tool builder (separate change)
- NOT supporting tool chaining/composition (agent loop handles this)
