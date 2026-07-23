# Spec: Agent Session

## Requirements

### SESS-001: Session model
The system MUST extend `ai.quest.session` with agent loop configuration and state.
- Session MUST store `AgentConfig` as serialized JSON
- Session MUST store message history as serialized JSON
- Session MUST track accumulated input and output tokens
- Session MUST track estimated cost based on model pricing

### SESS-002: Session lifecycle
The system MUST manage agent session lifecycle.
- A session MUST be creatable with a quest, agent, model, and optional tools
- A session MUST transition from `draft` → `active` → `done` (or `error`)
- Cancelled sessions MUST be marked appropriately with partial results preserved

### SESS-003: History persistence
The system MUST persist conversation history across loop invocations.
- History MUST include user messages, assistant messages, and tool results
- History MUST be loadable from the session for resuming or branching
- History size MUST be bounded (configurable `max_history_bytes`)

### SESS-004: Tool binding
The system MUST bind tools to sessions.
- Tools MUST be resolvable by name at execution time
- Tool binding MUST support Odoo model operations (search, read, write) via MCP
- Tool binding MUST support external MCP servers
- A session MUST be able to add/remove tools dynamically

## Non-requirements
- This spec does NOT cover the full Taskless verify/improve loop (separate spec)
- This spec does NOT cover multi-session orchestration (Paperclip-style)
