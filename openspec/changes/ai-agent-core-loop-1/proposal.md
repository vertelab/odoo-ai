# Change 1: AI Agent Core Loop & Provider Layer

## Summary

Build the absolute minimum for an ai_quest agent to run: the agent loop, Bifrost provider, direct provider, tool abstraction, and session management. This is the foundation — everything else builds on this.

## In scope (9 requirements)

### Agent Loop
- **LOOP-001**: `AgentLoop` — while-loop: prompt → provider → tools → repeat. ~200 lines.
- **LOOP-003**: Tool execution — sequential, bounded by `tool_timeout`. Errors return strings, never crash.
- **LOOP-004**: Auto-summarize on full context (one LLM call, Buzz-validated pattern).

### Provider Layer
- **PROV-001**: `AIProvider` ABC — `chat(model, messages, tools) → ChatResponse`, `chat_stream(model, messages, tools) → AsyncIterator[Token]`
- **PROV-002**: `BifrostProvider` — OpenAI-compatible via 192.168.11.150:8080/v1. Virtual key support.
- **PROV-003**: `DirectProvider` — Native access to OpenAI, Anthropic, DeepSeek. API keys from environment (set by Salt pillar).
- **PROV-006**: Retry logic — exponential backoff on 429, max 3 retries on 5xx.

### Tool System
- **TOOL-001**: `Tool` dataclass — `{name, description, parameters: JSONSchema, risk_level, handler}`. No LangChain inheritance.
- **TOOL-005**: Tool serialization — `to_openai_tool()` + `to_anthropic_tool()`. Lazy, provider-specific.

### Session
- **SESS-001**: `ai.quest.session` extended — config_json, history_json, token tracking.
- **SESS-002**: Session lifecycle — draft → active → done (or error).

### Human-in-the-Loop (minimal)
- **HITL-004**: `AutoInterruptHandler` — always approve, immediate timeout. For cron/server-action only.

## Out of scope (for this change)
- DiscussInterruptHandler, WebUIInterruptHandler (Change 2)
- Approval thresholds, mid-turn steering (Change 2)
- ai.identity, SOUL.md (Change 3)
- Skills, recipes, shared library (Change 3)
- pgGraph, graph_query tool (Change 3)
- OdooModelTools auto-generation, MCPTools (Change 3 — hardcoded tools räcker nu)
- Parallel tool execution, cancel, supervisor loop
- Model catalog sync

## Depends on
- httpx (new pip dependency)
- tenacity (already installed)
- Bifrost accessible at 192.168.11.150:8080
- ai_agent module (ai.quest, ai.agent models exist)

## Deliverables
1. `ai_agent_core/core/loop.py` — AgentLoop (~200 lines)
2. `ai_agent_core/core/provider.py` — AIProvider ABC + BifrostProvider + DirectProvider (~300 lines)
3. `ai_agent_core/core/tools.py` — Tool dataclass + serialization (~100 lines)
4. `ai_agent_core/core/context.py` — summarize on full context (~80 lines)
5. `ai_agent_core/models/ai_session.py` — extended session model (~100 lines)
6. `ai_agent_core/core/interrupt.py` — AutoInterruptHandler (~30 lines)
7. Unit tests for all of the above
8. One working quest running via cron using the new loop

**Estimated: 2-3 weeks**
