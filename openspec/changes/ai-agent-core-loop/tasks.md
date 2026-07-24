# Tasks: AI Agent Core Loop & Provider Layer

## Phase 1: Provider Layer (foundation) ✅ COMPLETE

### T1.1 — `core/provider.py` — AIProvider ABC + BifrostProvider ✅
- [x] Define `ChatResponse`, `ModelInfo` dataclasses
- [x] Define `AIProvider` ABC with `chat()`, `chat_stream()`, `fetch_models()`
- [x] Implement `BifrostProvider` using httpx
  - Chat completions (non-streaming)
  - Chat completions (streaming via SSE)
  - Model listing via GET /v1/models
  - Virtual key support (configurable)
  - Retry logic via tenacity
- [x] Write unit tests with mock Bifrost responses
- **Depends on:** httpx installed, Bifrost accessible

### T1.2 — `core/provider.py` — DirectProvider ✅
- [x] Implement `DirectProvider` for OpenAI (via httpx)
- [x] Implement `DirectProvider` for Anthropic (via httpx + Anthropic format)
- [x] Implement `DirectProvider` for Google, DeepSeek, Cerebras, Groq
- [x] Read API keys from environment variables (set by pillar)
- [x] Write unit tests with mock responses
- **Depends on:** T1.1

### T1.3 — `models/ai_model.py` — AIModel Odoo model ✅
- [x] Define `ai.model` Odoo model with provider_type, model names, capabilities
- [x] Add `fetch_models()` action that syncs from Bifrost
- [x] Add capability flags (vision, tools, json_mode, streaming)
- [x] Add cost fields (input/output per 1K tokens)
- [x] Create views (list, form)
- **Depends on:** T1.1

## Phase 2: Agent Loop (core engine) ✅ COMPLETE

### T2.1 — `core/loop.py` — AgentLoop ✅
- [x] Implement `AgentLoop` class with async run() method
- [x] Implement tool execution (parallel with semaphore, max_parallel_tools)
- [x] Implement context handoff/summarization
- [x] Implement cancellation support (cancel_event, cancel() method)
- [x] Implement token counting (estimate via char/4 + per-round logging)
- [x] Write unit tests with mock provider
- **Depends on:** T1.1

### T2.2 — `core/supervisor.py` — SupervisorLoop ✅ (2026-07-23)
- [x] Implement `SupervisorLoop` that routes to specialist agents
- [x] Implement router LLM call to choose agent
- [x] Support fan-out multi-agent (parallel execution + merge)
- [x] Keyword fallback when router LLM fails
- [x] Write unit tests
- **Depends on:** T2.1

### T2.3 — `core/tools.py` — ToolRegistry ✅
- [x] Define `Tool` dataclass with name, description, parameters, handler
- [x] Implement `ToolRegistry` with register/get methods
- [x] Implement Odoo model tools (search, read, write, create, unlink) via model_to_tools()
- [x] Implement MCP server tool registration via MCPToolDiscovery
- [x] Write unit tests
- **Depends on:** T1.1

## Phase 3: Session & Integration ✅ COMPLETE

### T3.0 — `core/interrupt.py` — InterruptHandler + implementations ✅
- [x] Define `InterruptHandler` ABC with `ask()`, `approve_tool()`, `drain_steer()`
- [x] Implement `DiscussInterruptHandler` (wait for discuss.channel message)
- [x] Implement `WebUIInterruptHandler` (SSE + HTTP POST)
- [x] Implement `AutoInterruptHandler` (always approve, immediate timeout)
- [x] Add `human_timeout` to AgentConfig (default 300s)
- [x] Add `approval_threshold` to AgentConfig
- [x] Add `risk_level` to Tool dataclass
- [x] Write unit tests with mock handlers
- **Depends on:** T2.1, T2.3

### T3.1 — `models/ai_session.py` — Extended session ✅
- [x] Extend `ai.quest.session` with config_json, history_json, token tracking
- [x] Implement history serialization/deserialization
- [x] Implement session lifecycle (draft → active → done)
- [x] Add views to show token usage and cost
- **Depends on:** T2.1, T2.3

### T3.2 — `controllers/stream.py` — SSE endpoint ✅
- [x] Replace mock stream with real BifrostProvider.chat_stream()
- [x] Wire AgentLoop.run_stream() to SSE endpoint
- [x] Support quest_id parameter to select quest/model
- [x] Support cancel via client disconnect
- **Depends on:** T1.1, T2.1

### T3.3 — Migrate one quest to use new loop ✅ (2026-07-23)
- [x] Add `use_core_loop` boolean on ai.quest
- [x] In quest.run(), branch: old LangGraph vs new AgentLoop
- [x] AIQuestRun wizard for testing via Odoo UI
- [x] Session creation on run
- **Depends on:** T2.1, T3.2

## Phase 4: Polish 🟡 IN PROGRESS

### T4.1 — Error handling & observability ✅ (2026-07-23)
- [x] Add structured logging for all provider calls (per-round timing, token totals)
- [x] Add token usage tracking per session
- [x] Add cost estimation in session views
- **Depends on:** T3.1

### T4.2 — Documentation 🟡
- [ ] Update module README
- [x] Add docstrings to all public methods
- [ ] Create architecture diagram
- **Depends on:** T3.3

---

## Change 2: Agent Identity, Skills & Taskless (ai-agent-skills-identity)

### Phase 1: Standalone ai_agent_core ✅
- [x] T1.1 — Update `__manifest__.py` — remove ai_agent dependency, add own menu/security
- [x] T1.2 — Create `security/ir.model.access.csv` with standalone access rights
- [x] T1.3 — Create menu + actions (AI Orchestration)

### Phase 2: Identity (SOUL.md) ✅
- [x] T2.1 — `models/ai_identity.py` — ai.identity model (soul, user_model, skills)
- [x] T2.2 — `views/ai_identity_views.xml` — form/list/kanban views
- [x] T2.3 — System prompt compilation from identity components

### Phase 3: Skills ✅
- [x] T3.1 — `models/ai_skill.py` — ai.skill model (name, triggers, recipes, verify_cases)
- [x] T3.2 — `views/ai_skill_views.xml` — skill views
- [x] T3.3 — Skill assignment to agents/quests

### Phase 4: Taskless & Tools ✅ (2026-07-23)
- [x] T4.1 — Custom tools: `models/ai_tool.py` + `views/ai_tool_views.xml`
- [x] T4.2 — OdooModelTools factory: `core/tools.py` model_to_tools()
- [x] T4.3 — MCPTools: `core/tools.py` MCPToolDiscovery
- [x] T4.4 — Migration bridge: `use_core_loop` + AIQuestRun wizard
- [ ] T4.5 — `core/detect.py` — scan before acting
- [ ] T4.6 — `core/route.py` — intelligent path selection
- [ ] T4.7 — `core/improve.py` — structured feedback loop
- [ ] T4.8 — `core/verify.py` — three-layer validation

---

## Remaining (not yet implemented)

| Feature | Priority | Estimate |
|---------|----------|----------|
| Taskless DETECT/ROUTE/IMPROVE/VERIFY/ONBOARD | Next | 1-2 weeks |
| Budget enforcement (PAPER-004) | Next | 3 days |
| Clarification requests (HITL-007) | Next | 2 days |
| Intercom parent-child (HITL-009) | Next | 3 days |
| Review loop (HITL-010) | Next | 3 days |
| Evals & feedback (PAPER-006) | Later | 1 week |
| Kaizen loop (weekly self-review) | Later | 3 days |
| README + architecture diagram | Soon | 2 hours |
