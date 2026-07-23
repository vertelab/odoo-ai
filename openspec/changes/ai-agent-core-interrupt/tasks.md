# Tasks: Human-in-the-Loop (Change 2)

## Phase 1: InterruptHandler ABC + Discuss

- [x] **T1.1** — `core/interrupt.py` — InterruptHandler ABC (ask, approve_tool, drain_steer)
- [x] **T1.2** — `core/interrupt.py` — DiscussInterruptHandler (wait for channel message)
- [x] **T1.3** — Wire DiscussInterruptHandler into ai.quest.chat()

## Phase 2: WebUI + Approval

- [x] **T2.1** — `core/interrupt.py` — WebUIInterruptHandler (SSE + POST)
- [x] **T2.2** — Approval threshold per tool (risk_level → HITL-005)
- [x] **T2.3** — Mid-turn steering (drain_steer)

## Phase 3: UI enhancements

- [x] **T3.1** — Cancel button + interrupt dialog in chat UI
- [x] **T3.2** — Approval buttons for tool calls
- [x] **T3.3** — Token/cost display

## Phase 4: Integration

- [x] **T4.1** — Wired into discuss.channel.message_post()
- [x] **T4.2** — 23 unit tests (inherited from Change 1)
