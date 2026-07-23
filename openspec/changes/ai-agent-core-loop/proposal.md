# Proposal: AI Agent Core Loop & Provider Layer

## Summary

Build the foundational layer of `ai_agent_core` — a refactored AI agent engine for Odoo that replaces the LangGraph-based monolith in `ai_agent` with a modular, Buzz-inspired architecture. This change covers the three core subsystems: agent loop, provider layer, and agent session management.

## Motivation

The current `ai_agent` module uses LangGraph (StateGraph + Supervisor + LangChain) wrapped in `ai_quest.py` (1527 lines). This creates problems:

1. **LangChain sunset** — `langchain-community` is deprecated, causing warnings at every Odoo startup
2. **Hard to debug** — Graph-based state machines are opaque; errors are buried in LangChain stack traces
3. **Tight coupling** — Provider logic, tool calling, and loop control are intertwined in a single file
4. **No feedback loop** — No structured mechanism for iterating on quest results (Taskless improve/verify pattern)
5. **Excessive dependencies** — 20+ LangChain packages for features we use 5% of

Buzz-agent (Block/Square, Rust, Apache 2.0) proves that a production-grade agent loop is ~800 lines with zero framework dependencies. The loop is a simple `while` loop: prompt LLM → execute tools → repeat. Context management is built-in (handoff/summarize). No graph compiler needed.

## Goals

- **MUST**: Build a Buzz-inspired `AgentLoop` (`core/loop.py`, ~200 lines) that replaces LangGraph StateGraph
- **MUST**: Build a `BifrostProvider` (`core/provider.py`, ~150 lines) that communicates with Bifrost via OpenAI-compatible API
- **MUST**: Support streaming (SSE) from the agent loop to frontend controllers
- **MUST**: Provide a `SupervisorLoop` for multi-agent quests (routes user prompt → selects agent → runs loop)
- **SHOULD**: Support `DirectProvider` as fallback for features Bifrost doesn't proxy (vision, embeddings, TTS)
- **SHOULD**: Implement context handoff/summarization when token budget is exceeded
- **SHOULD**: Support parallel tool execution with configurable concurrency limit
- **MAY**: Add `ai.model` auto-sync from Bifrost's `/v1/models` endpoint

## Non-Goals

- NOT replacing LangGraph in the existing `ai_agent` module (that's a migration, not this change)
- NOT building the full Taskless layer (detect/route/improve/verify — separate changes)
- NOT building the frontend chat UI (separate `ai_agent_chat` prototype exists)
- NOT touching `ai_agent_mcp` or `ai_agent_context` (plugins that will depend on core later)
- NOT adding file/image upload to the loop (separate change)

## Dependencies

- `httpx` (async HTTP) — new pip dependency
- `tenacity` (retry logic) — already installed
- `Pydantic v2` (validation) — already installed via Odoo
- Bifrost LLM Gateway — already deployed at 192.168.11.150:8080
- `ai_agent` module — core models (ai.quest, ai.agent, ai.quest.session) are inherited
