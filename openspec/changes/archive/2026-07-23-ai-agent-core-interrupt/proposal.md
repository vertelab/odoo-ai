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

### Web UI (interrupt + media)
- **UI-010**: Cancel button — stops in-progress agent run mid-turn.
- **UI-011**: Interrupt dialog — modal showing agent's question, input field for response, timeout indicator.
- **UI-012**: Approval buttons — accept/reject on tool calls requiring human approval.
- **UI-013**: File upload — attach documents (PDF, XLSX, DOCX) to chat context. Supported via drag-and-drop or file picker.
- **UI-014**: Image upload — attach images (PNG, JPG, WebP) with preview. Forwards to vision-capable models.
- **UI-015**: Regenerate response — re-run last prompt with same or different model.
- **UI-016**: Token counter — display tokens used per message and total for session.
- **UI-017**: Cost display — estimated cost per session based on model pricing.

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
