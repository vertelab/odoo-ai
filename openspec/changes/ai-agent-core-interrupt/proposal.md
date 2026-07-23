# Change 2: Human-in-the-Loop & Context Management

## Summary

Add full human-in-the-loop support and context management to the agent loop. Enables chat-based quests where humans can interrupt, approve, and steer agent behavior.

## In scope (6 requirements)

### Interrupt Layer
- **HITL-001**: `InterruptHandler` ABC — `ask()`, `approve_tool()`, `drain_steer()`
- **HITL-002**: `DiscussInterruptHandler` — wait for discuss.channel message, timeout → auto-continue
- **HITL-003**: `WebUIInterruptHandler` — SSE event → frontend dialog → POST response
- **HITL-005**: Approval threshold — risk_level per tool (safe/read_only/write/destructive/execute)
- **HITL-006**: Mid-turn steering — `drain_steer()` non-blocking message injection

### Context Management
- **LOOP-004** (extended): Context handoff with configurable `max_context_tokens`

## Out of scope
- Clarification requests (Hermes pattern)
- Intercom parent-child
- Review loops
- Supervisor loop, parallel tool execution

## Depends on
- Change 1 (core loop + provider)

## Deliverables
1. `ai_agent_core/core/interrupt.py` — full InterruptHandler implementations
2. `ai_agent_core/core/context.py` — extended context management
3. SSE endpoint with interrupt support
4. Integration tests with discuss.channel

**Estimated: 1-2 weeks**
