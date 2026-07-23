# Spec: Human-in-the-Loop (Interrupt Layer)

## Requirements

### HITL-001 [NOW] — Interrupt handler abstraction
The system MUST provide an `InterruptHandler` abstract base class for human-in-the-loop interactions.
- `InterruptHandler.ask(question, approval_type, context, timeout) → AskResult` — BLOCKING: pause loop, wait for human
- `InterruptHandler.approve_tool(tool_call) → bool` — BLOCKING: ask if a tool call may proceed
- `InterruptHandler.drain_steer() → list[str]` — NON-BLOCKING: fetch queued mid-turn messages (Buzz pattern)
- The loop MUST NOT proceed past an interrupt until the handler returns or times out

### HITL-002 [NOW] — DiscussInterruptHandler (Odoo chat)
The system MUST provide an interrupt handler for Odoo discuss channels.
- When agent needs input: post a question as a message in the channel, then WAIT
- When human responds: parse the response, inject as `HumanMessage`, continue loop
- Timeout behaviour: after `human_timeout` (default 5 min), auto-continue with best judgment
- Mid-turn steer: `drain_steer()` reads new messages from the channel without pausing

### HITL-003 [NOW] — WebUIInterruptHandler (/ai/chat)
The system MUST provide an interrupt handler for the web-based chat UI.
- When agent needs input: emit SSE event `needs_clarification` or `needs_approval`
- Frontend renders a modal/dialog with the question and approval buttons
- Waits for HTTP POST to `/ai/session/{id}/respond`
- Timeout: same `human_timeout` behaviour
- Cancel: client disconnect stops the wait

### HITL-004 [NOW] — AutoInterruptHandler (cron, server-action)
The system MUST provide an auto-approving interrupt handler for unattended execution.
- `ask()`: returns immediately with timeout → continue
- `approve_tool()`: returns `True` for all tool calls (auto-approve)
- `drain_steer()`: always returns empty list
- Used for: cron jobs, server actions, email-triggered quests

### HITL-005 [NOW] — Approval threshold per tool
The system MUST support configurable approval thresholds for tool calls.
- Each tool has a `risk_level`: `safe` | `read_only` | `write` | `destructive`
- `approval_threshold` on agent/quest: tools at or above this level require human approval
- `safe` tools (read-only lookups) NEVER require approval
- `destructive` tools (delete, unlink) ALWAYS require approval regardless of threshold
- The threshold is configurable per quest and per init_type

### HITL-006 [NOW] — Mid-turn steering (Buzz pattern)
The system MUST support non-blocking mid-turn message injection.
- `drain_steer()` queues human messages during an active turn without restarting
- Steer messages appear as `HumanMessage` in the next LLM request
- The turn continues — it is NOT restarted
- An empty steer queue is the common case (zero overhead)

### HITL-007 [NEXT] — Clarification requests (Hermes pattern)
The agent SHOULD be able to proactively ask clarifying questions.
- Agent sets `response.needs_clarification = True` with a specific question
- InterruptHandler presents the question and waits for answer
- The answer is injected into the conversation and the loop continues
- Agent can ask up to `max_clarifications` times (default 3) before proceeding

### HITL-008 [NEXT] — Approval gates for critical outputs
The system SHOULD support explicit approval before critical actions.
- Agent marks output as `needs_approval` with `approval_type`:
  - `create_record`: creating new Odoo records
  - `modify_record`: modifying existing records
  - `delete_record`: deleting records
  - `send_email`: sending external communication
  - `execute_code`: running generated code
- Human approves/rejects → agent continues or backs out
- Approval is a first-class workflow step, part of the audit trail

### HITL-009 [NEXT] — Intercom (Pi-subagents pattern)
The system SHOULD support parent-child intercom for delegated quests.
- Child quest can call `contact_supervisor(reason, message)` when blocked
- Parent receives the message and can reply via `subagent_supervisor.reply()`
- `reason: "need_decision"` — blocked on a choice → pause → wait for parent reply → continue
- `reason: "interview_request"` — needs structured input → wait → continue
- `reason: "progress_update"` — non-blocking status update
- Child must NOT continue past the interrupt until parent replies

### HITL-010 [NEXT] — Review loop (Pi-subagents pattern)
The system SHOULD support automated review loops with human escalation.
- Worker quest produces output → reviewer quests inspect → issues found → fix → re-review
- Max 3 review rounds before escalating to human
- Each round: parallel reviewers with fresh context, distinct review angles
- Parent synthesizes findings, decides which to fix
- Stops when: no blockers found, remaining feedback is optional, or cap reached

## Non-requirements
- NOT building a real-time collaboration system (multiple humans editing simultaneously)
- NOT implementing video/voice interrupt handling (future)
- NOT implementing interrupt for file uploads mid-turn (separate spec)
