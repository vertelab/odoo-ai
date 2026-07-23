# Tasks: AI Agent Core Loop & Provider Layer

## Phase 1: Provider Layer (foundation)

### T1.1 — `core/provider.py` — AIProvider ABC + BifrostProvider
- [ ] Define `ChatResponse`, `ModelInfo` dataclasses
- [ ] Define `AIProvider` ABC with `chat()`, `chat_stream()`, `fetch_models()`
- [ ] Implement `BifrostProvider` using httpx
  - Chat completions (non-streaming)
  - Chat completions (streaming via SSE)
  - Model listing via GET /v1/models
  - Virtual key support (configurable)
  - Retry logic via tenacity
- [ ] Write unit tests with mock Bifrost responses
- **Depends on:** httpx installed, Bifrost accessible

### T1.2 — `core/provider.py` — DirectProvider
- [ ] Implement `DirectProvider` for OpenAI (via openai SDK or httpx)
- [ ] Implement `DirectProvider` for Anthropic (via anthropic SDK or httpx)
- [ ] Implement `DirectProvider` for Google (via httpx)
- [ ] Read API keys from environment variables (set by pillar)
- [ ] Write unit tests with mock responses
- **Depends on:** T1.1

### T1.3 — `models/ai_model.py` — AIModel Odoo model
- [ ] Define `ai.model` Odoo model with provider_type, model names, capabilities
- [ ] Add `fetch_models()` action that syncs from Bifrost
- [ ] Add capability flags (vision, tools, json_mode, streaming)
- [ ] Add cost fields (input/output per 1K tokens)
- [ ] Create views (list, form)
- **Depends on:** T1.1

## Phase 2: Agent Loop (core engine)

### T2.1 — `core/loop.py` — AgentLoop
- [ ] Implement `AgentLoop` class with async run() method
- [ ] Implement tool execution (parallel with semaphore)
- [ ] Implement context handoff/summarization
- [ ] Implement cancellation support
- [ ] Implement token counting (tiktoken)
- [ ] Write unit tests with mock provider
- **Depends on:** T1.1

### T2.2 — `core/supervisor.py` — SupervisorLoop
- [ ] Implement `SupervisorLoop` that routes to specialist agents
- [ ] Implement router LLM call to choose agent
- [ ] Support sequential multi-agent (agent A → agent B)
- [ ] Write unit tests
- **Depends on:** T2.1

### T2.3 — `core/tools.py` — ToolRegistry
- [ ] Define `Tool` dataclass with name, description, parameters, handler
- [ ] Implement `ToolRegistry` with register/get methods
- [ ] Implement Odoo model tools (search, read, write)
- [ ] Implement MCP server tool registration
- [ ] Write unit tests
- **Depends on:** T1.1

## Phase 3: Session & Integration

### T3.0 — `core/interrupt.py` — InterruptHandler + implementations
- [ ] Define `InterruptHandler` ABC with `ask()`, `approve_tool()`, `drain_steer()`
- [ ] Implement `DiscussInterruptHandler` (wait for discuss.channel message)
- [ ] Implement `WebUIInterruptHandler` (SSE + HTTP POST)
- [ ] Implement `AutoInterruptHandler` (always approve, immediate timeout)
- [ ] Add `human_timeout` to AgentConfig (default 300s)
- [ ] Add `approval_threshold` to AgentConfig
- [ ] Add `risk_level` to Tool dataclass
- [ ] Write unit tests with mock handlers
- **Depends on:** T2.1, T2.3

### T3.1 — `models/ai_session.py` — Extended session
- [ ] Extend `ai.quest.session` with config_json, history_json, token tracking
- [ ] Implement history serialization/deserialization
- [ ] Implement session lifecycle (draft → active → done)
- [ ] Add views to show token usage and cost
- **Depends on:** T2.1, T2.3

### T3.2 — `controllers/stream.py` — SSE endpoint (replace mock)
- [ ] Replace mock stream with real BifrostProvider.chat_stream()
- [ ] Wire AgentLoop.run_stream() to SSE endpoint
- [ ] Support quest_id parameter to select quest/model
- [ ] Support cancel via client disconnect
- **Depends on:** T1.1, T2.1

### T3.3 — Migrate one quest to use new loop
- [ ] Add `use_core_loop` boolean on ai.quest
- [ ] In quest.run(), branch: old LangGraph vs new AgentLoop
- [ ] Test with existing "customer analysis" quest
- [ ] Verify streaming works end-to-end
- **Depends on:** T2.1, T3.2

## Phase 4: Polish

### T4.1 — Error handling & observability
- [ ] Add structured logging for all provider calls
- [ ] Add token usage tracking per session
- [ ] Add cost estimation in session views
- **Depends on:** T3.1

### T4.2 — Documentation
- [ ] Update module README
- [ ] Add docstrings to all public methods
- [ ] Create architecture diagram
- **Depends on:** T3.3
