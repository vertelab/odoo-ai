# Tasks: AI Agent Core Loop & Provider Layer (Change 1)

## Phase 1: Foundation (provider + tools)

- [x] **T1.1** — `core/provider.py` — AIProvider ABC + BifrostProvider + DirectProvider + retry
- [x] **T1.2** — `core/tools.py` — Tool dataclass + serialization (TOOL-001, TOOL-005)
- [x] **T1.3** — `core/__init__.py` — module init

## Phase 2: Agent Loop

- [x] **T2.1** — `core/loop.py` — AgentLoop (while-loop, ~200 lines)
- [x] **T2.2** — `core/context.py` — Summarize on full context (LOOP-004)
- [x] **T2.3** — `core/interrupt.py` — AutoInterruptHandler (HITL-004)

## Phase 3: Session & Integration

- [x] **T3.1** — `models/ai_session.py` — Extended session model (SESS-001, SESS-002)
- [x] **T3.2** — Replace mock stream with real BifrostProvider in SSE controller
- [ ] **T3.3** — Wire AgentLoop into ai.quest.run() for one quest
- [ ] **T3.4** — Test end-to-end: quest runs via cron with new loop

## Phase 4: Polish

- [ ] **T4.1** — Unit tests for provider, tools, loop, context
- [ ] **T4.2** — Documentation and logging
