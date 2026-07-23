# Tasks: Human-in-the-Loop (Change 2)

## Phase 1: InterruptHandler ABC + Discuss

- [x] **T1.1** — `core/interrupt.py` — InterruptHandler ABC (ask, approve_tool, drain_steer)
- [x] **T1.2** — `core/interrupt.py` — DiscussInterruptHandler (wait for channel message)
- [x] **T1.3** — Wire DiscussInterruptHandler into ai.quest.chat()

## Phase 2: WebUI + Approval

- [ ] **T2.1** — `core/interrupt.py` — WebUIInterruptHandler (SSE + POST)
- [ ] **T2.2** — Approval threshold per tool (risk_level → HITL-005)
- [ ] **T2.3** — Mid-turn steering (drain_steer)

## Phase 3: UI enhancements

- [ ] **T3.1** — Cancel button + interrupt dialog in chat UI
- [ ] **T3.2** — Approval buttons for tool calls
- [ ] **T3.3** — File/image upload + token/cost display

## Phase 4: Integration

- [ ] **T4.1** — Integration test with discuss.channel
- [ ] **T4.2** — Unit tests for interrupt handlers
