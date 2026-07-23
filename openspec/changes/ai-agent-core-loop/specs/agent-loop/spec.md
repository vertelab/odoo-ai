# Spec: Agent Loop

## Requirements

### LOOP-001: Basic agent loop
The system MUST provide an `AgentLoop` class that implements a prompt→tools→repeat cycle.
- The loop MUST accept a prompt string, message history, and tool list
- The loop MUST call the provider's `chat()` method with messages + tool definitions
- The loop MUST execute tool calls returned by the provider
- The loop MUST append tool results to the message history
- The loop MUST return the final text response when no more tool calls are requested
- The loop MUST respect `max_rounds` configuration (default 0 = unlimited)

### LOOP-002: Streaming support
The system MUST support streaming responses from the agent loop.
- `AgentLoop.run_stream()` MUST yield tokens via async generator
- Streamed tokens MUST include text content and tool call deltas
- The SSE controller MUST consume the async generator and emit SSE events
- Streaming MUST be cancellable (client disconnect stops the loop)

### LOOP-003: Tool execution
The system MUST execute tools in parallel with configurable concurrency.
- Tool calls from a single provider response MUST be executed concurrently (up to `max_parallel_tools`)
- Failed tool executions MUST return error messages rather than crashing the loop
- Each tool execution MUST be bounded by `tool_timeout` (seconds)
- Tool results MUST be truncated if they exceed `max_tool_result_bytes`

### LOOP-004: Context management
The system SHOULD implement automatic context handoff when the token budget is exceeded.
- When estimated context tokens exceed `max_context_tokens`, the agent MUST summarize history
- Summarization MUST preserve key facts from the conversation
- After summarization, the loop MUST continue with compressed context
- The original task description MUST be preserved across handoffs

### LOOP-005: Cancellation
The system SHOULD support mid-turn cancellation.
- A cancel signal MUST stop the current LLM call
- A cancel signal MUST stop pending tool executions
- Partial results from cancelled tool executions MUST be available

### LOOP-006: Mid-turn steering
The system MAY support mid-turn steering (appending user messages while the loop runs).
- Queued steer messages MUST be consumed at each loop round boundary
- Steer messages MUST appear as user turns in the next LLM request

## Non-requirements
- This spec does NOT cover multi-agent supervisor routing (see supervisor-loop spec)
- This spec does NOT cover provider implementation details (see provider-layer spec)
