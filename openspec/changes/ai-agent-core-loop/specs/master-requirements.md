# Spec: Master Requirements (Buzz + Paperclip + Taskless)

This is the unified requirements list for `ai_agent_core`, derived from studying:

- **Buzz** (Block/Square) — how the agent works internally
- **Paperclip** (paperclipai/paperclip) — how agents are orchestrated as a team
- **Taskless** (taskless/skills) — how agents learn and improve

Requirements marked **[NOW]** are in scope for the current change (`ai-agent-core-loop`).
Requirements marked **[NEXT]** are planned for subsequent changes.
Requirements marked **[LATER]** are future roadmap.

---

## Buzz: Agent Internals

### BUZZ-001 [NOW] — Agent = while-loop, not a framework
The agent MUST be a simple `while` loop: prompt → execute tools → repeat.
- No StateGraph, no LangGraph, no LangChain dependency.
- The entire agent loop MUST be auditable in a single sitting (~200 lines).

### BUZZ-002 [NOW] — Provider as enum/ABC
The provider layer MUST be an abstract interface with concrete implementations.
- `AIProvider.chat(model, messages, tools)` → `ChatResponse`
- `BifrostProvider` for OpenAI-compatible gateway access
- `DirectProvider` for native provider features

### BUZZ-003 [NEXT] — Tools via protocol (MCP), not imports
Tools MUST be discoverable via protocol, not hard-coded imports.
- MCP servers provide tool definitions at runtime
- Same interface regardless of tool type (Odoo model, shell command, API call)
- Tool isolation: one tool crash MUST NOT crash the agent

### BUZZ-004 [NOW] — Auto-summarize on full context
When context exceeds the model's window, the agent MUST summarize its own history.
- One LLM call for summarization
- Original task description preserved across handoffs
- Configurable token budget (`max_context_tokens`)

### BUZZ-005 [NOW] — Isolated parallel sessions
Multiple quest sessions MUST run independently.
- Each session has its own tool set, message history, and configuration
- Sessions MUST NOT share state unless explicitly configured

### BUZZ-006 [NOW] — Timeout + graceful degradation
Every tool execution MUST have a timeout.
- `tool_timeout` per tool (seconds)
- `llm_timeout` per LLM call
- Timeout MUST produce an error message, not crash the session

### BUZZ-007 [NOW] — Parallel tool execution
Multiple tool calls from a single LLM response MUST execute concurrently.
- Configurable `max_parallel_tools` (semaphore)
- Results collected in original call order

### BUZZ-008 [NOW] — Cancel mid-turn
The system MUST support cancelling an in-progress agent turn.
- Cancel stops current LLM call
- Cancel stops pending tool executions
- Partial results preserved

### BUZZ-009 [LATER] — Mid-turn steering
The system MAY support injecting user messages mid-turn.
- Queued steer messages consumed at loop round boundaries
- Appear as user turns in the next LLM request

---

## Paperclip: Agent Orchestration

### PAPER-001 [LATER] — BYO-Agent adapter
The system SHOULD support plugging in different agent runtimes.
- Adapter interface: `run(prompt, context) → result`
- Built-in: our `AgentLoop`
- External: Claude Code, Codex, shell scripts
- Agents register via adapter plugin

### PAPER-002 [NEXT] — Skills Manager + Skill Studio
The system MUST provide a skills framework.
- Skills are reusable competencies (not just tools)
- Skills have: trigger keywords, recipe files, verify test cases
- Skills are versioned and shareable across the organization
- Skills can be created, tested, and published via Skill Studio

### PAPER-003 [LATER] — Heartbeat-based agent lifecycle
Agents SHOULD be able to wake on schedule and act autonomously.
- `heartbeat_schedule` on agent: cron expression
- On heartbeat: check for work → act → report
- Failed heartbeats MUST surface as alerts

### PAPER-004 [NEXT] — Budgets with hard stops
Every agent MUST have a configurable budget.
- `budget_limit` per agent per month (in tokens or dollars)
- When budget exhausted: agent stops, no further LLM calls
- Budget usage MUST be visible in agent dashboard
- Budget is a HARD stop, not a notification

### PAPER-005 [NEXT] — Approval gates
Critical agent outputs SHOULD require human approval.
- Agent can mark output as `needs_approval`
- Human approves/rejects → agent continues or backs out
- Approval is a first-class workflow step, not a comment

---

## Human-in-the-Loop (Buzz + Pi-subagents + Hermes)

### HITL-001 [NOW] — Interrupt handler abstraction
The system MUST provide an `InterruptHandler` ABC for blocking and non-blocking human interaction.
- `ask(question, approval_type, context, timeout) → AskResult` — BLOCKING
- `approve_tool(tool_call) → bool` — BLOCKING per tool
- `drain_steer() → list[str]` — NON-BLOCKING mid-turn input (Buzz pattern)

### HITL-002 [NOW] — DiscussInterruptHandler (Odoo chat)
The system MUST provide an interrupt handler for Odoo discuss channels.
- Post question as channel message → wait for human response
- Timeout: human_timeout (default 5 min) → auto-continue with best judgment

### HITL-003 [NOW] — WebUIInterruptHandler (/ai/chat)
The system MUST provide an interrupt handler for web-based chat.
- Emit SSE event → frontend shows dialog → wait for POST response
- Cancel on client disconnect

### HITL-004 [NOW] — AutoInterruptHandler (cron, server-action)
The system MUST provide an auto-approving handler for unattended execution.
- Returns immediately: auto-continue, auto-approve all tools

### HITL-005 [NOW] — Approval threshold per tool
The system MUST support configurable tool approval thresholds.
- risk_level: safe | read_only | write | destructive
- approval_threshold: tools at/above this level require human
- safe tools NEVER require approval; destructive tools ALWAYS require it

### HITL-006 [NOW] — Mid-turn steering (Buzz pattern)
The system MUST support non-blocking mid-turn message injection.
- drain_steer() queues messages during active turn
- Appears as HumanMessage in next LLM request
- Turn continues, not restarted

### HITL-007 [NEXT] — Clarification requests (Hermes pattern)
The agent SHOULD proactively ask clarifying questions.
- needs_clarification flag → interrupt → human answers → continue
- Max max_clarifications (default 3) before proceeding

### HITL-008 [NEXT] — Approval gates for critical outputs
The system SHOULD support explicit approval for: create_record, modify_record, delete_record, send_email, execute_code.

### HITL-009 [NEXT] — Intercom (Pi-subagents pattern)
Child quests SHOULD be able to contact parent when blocked.
- contact_supervisor(reason, message)
- reason: need_decision | interview_request | progress_update
- Child must NOT continue past interrupt until parent replies

### HITL-010 [NEXT] — Review loop (Pi-subagents + Taskless)
The system SHOULD support automated review loops: worker → reviewers → fix → re-review.
- Max 3 rounds before human escalation
- Parallel reviewers with distinct angles, fresh context
- Stops when no blockers or cap reached

---

## Agent Identity (SOUL.md + Memory + Skills)

### ID-001 [NOW] — Identity model
ai.identity bundles soul + user_model + memory + skills → compiled system_prompt.

### ID-002 [NOW] — Soul definition
Four dimensions: personality, style, values, boundaries. Editable by owner.

### ID-003 [NEXT] — User model
Persistent model of the user. Private to quest owner. Updated via /learn.

### ID-004 [NOW] — Identity scope
personal | organization | public. Determines visibility, editability, memory isolation.

### ID-005 [NOW] — System prompt compilation
Compiled at session start: soul → user_model → memory → skills → tools.
Cached per session.

### ID-006 [NEXT] — Identity learning (Hermes /learn)
Update identity components through interaction. User confirmation required.

### ID-007 [NEXT] — Memory integration
Bind to ai.memory. Recent memories injected as context. Memory search as tool.

### ID-008 [NOW] — Identity templates
Shipped templates for common agent types. Versioned and upgradable.

### PAPER-006 [NEXT] — Evals & feedback
Agent performance MUST be measurable over time.
- Per-agent statistics: precision, recall, cost per correct answer
- False positive/negative tracking
- Evaluation runs with saved results
- Feedback loop for continuous improvement

### PAPER-007 [NOW] — Activity log (audit trail)
Every agent action MUST be traceable.
- Every LLM call logged (tokens, cost, model, provider)
- Every tool execution logged (tool name, arguments, result)
- Every state change logged (session start/end, status changes)
- Log MUST be immutable (append-only)
- NOTE: `ai.quest.session.line` already provides this — extend it

### PAPER-008 [NOW] — Plugin system (thin core, rich edges)
The core module MUST be minimal; all extensions are plugins.
- `ai_agent_core` = loop + provider + session
- `ai_agent_context` = plugin (record injection)
- `ai_agent_mcp` = plugin (MCP bridge)
- `ai_mail_e_avrop` = plugin (mail parsing)
- `ai_agent_trend` = plugin (analytics)
- Plugins depend on core, never the reverse

---

## Tool System (without LangChain)

### TOOL-001 [NOW] — Tool abstraction
Plain Python objects. OpenAI function-calling format. No LangChain BaseTool inheritance.

### TOOL-002 [NOW] — Tool types
OdooModelTools (auto-generated per model), MCPTools (discovered from servers), CustomTools (user-defined).

### TOOL-003 [NOW] — Tool registration
ToolRegistry aggregates from all sources. Name-based lookup. Idempotent registration.

### TOOL-004 [NOW] — Tool execution
Bounded by tool_timeout. Errors return strings, never crash loop. Truncated at max bytes.

### TOOL-005 [NOW] — Tool serialization
Provider-native formats: OpenAI function-calling, Anthropic input_schema. Lazy serialization.

### TOOL-006 [NOW] — Risk levels
safe | read_only | write | destructive | execute. Drives InterruptHandler approval.

### TOOL-007 [NEXT] — Dynamic discovery
Mid-session tool registration. MCP servers register tools dynamically.

### TOOL-008 [NEXT] — Tool documentation
Auto-generated from definitions. Usage examples. Recipes in recipes/tools/.

---

## Shared Skills & Agent Administration

### SHARE-001 [NOW] — Unified recipe library
/srv/salt/ai/recipes/ — canonical source. All agents read the same files.

### SHARE-002 [NOW] — Thin router skills
Skills are trigger+topic table. No embedded procedures. References recipes.

### SHARE-003 [NEXT] — Recipe migration
Extract from ~74 existing skills. Remove duplicates. Replace with routers.

### SHARE-004 [NOW] — Agent administration in Odoo
All agent types managed in same UI: ai.agent kanban/list/form.

### SHARE-005 [NOW] — Python Pi-agent administration
Register, start/stop, monitor, configure skills — all from Odoo.

### SHARE-006 [NOW] — Skill assignment UI
Unified skill picker. Same catalog for ai_quest and Pi-agents.

### SHARE-007 [NEXT] — Skill catalog sync
Auto-create ai.skill from recipe frontmatter. Version tracking.

### SHARE-008 [NOW] — Agent type model
ai.agent.agent_type: odoo | pi_python | pi_node | opencode | external.

---

## Graph Data (odoograph via pgGraph)

### GRAPH-001 [NOW] — Graph backend via pgGraph
PostgreSQL extension. Same DB as Odoo, zero network latency. Replaces Neo4j.

### GRAPH-002 [NOW] — Graph query tool
graph_query(cypher, params) → structured data. risk_level: read_only.

### GRAPH-003 [NOW] — Odoo access rights in graph
pgGraph source-table ACL checks respect Odoo's PostgreSQL privileges. No bypass.

### GRAPH-004 [NOW] — Per-user tenant isolation
tenant_column = company_id (records) / user_id (email). graph.tenant_setting at session start.

### GRAPH-005 [NOW] — Email graph integration
Email data in graph with personal scoping. Person→Email→Person relationships.

### GRAPH-006 [NOW] — Odoo model registration
Core models registered as graph nodes at install. Idempotent. Extensible.

### GRAPH-007 [NEXT] — Graph sync from odoograph
Keep graph in sync with live Odoo changes. Poll-based or pgGraph sync policies.

### GRAPH-008 [NEXT] — Graph skill recipes
Port Hermes Cypher queries to recipes/graph/. Agents use as skill references.

---

## Taskless: Agent Learning

### TASK-001 [NEXT] — DETECT: scan before acting
Before creating or modifying a quest, the system MUST scan the environment.
- Which quests already exist? (→ improve instead of create)
- Which Odoo modules are installed? (→ context)
- Which data quality issues exist? (→ quest candidates)
- Which providers/models are available? (→ capability matching)
- Output MUST be deterministic JSON, offline-capable

### TASK-002 [NEXT] — ROUTE: intelligent path selection
When a user requests a new quest/action, the system MUST decide: existing → local → remote.
- `existing`: a quest already covers this → use it
- `local`: can be solved with current tools/configuration → do it locally
- `remote`: needs external LLM generation → call provider (costs tokens)
- Agent MUST write its reasoning BEFORE naming the destination
- Local-first bias: don't spend API calls unnecessarily

### TASK-003 [NEXT] — IMPROVE: structured feedback loop
When quest output is incorrect, the system MUST support structured improvement.
- `guidance`: human-readable feedback ("missed Swedish customers")
- `references`: concrete examples [{filename, content}] for false positives/negatives
- Agent iterates with guidance + references → verify
- Max 3 iterations before escalating to human
- Both API-backed and locally-anonymous flows

### TASK-004 [NEXT] — VERIFY: three-layer validation
Every quest output MUST pass three validation layers.
- **Schema**: output format matches expected structure
- **Requirements**: all required fields present, dependencies satisfied
- **Tests**: test cases pass (valid inputs → expected outputs, invalid inputs → flagged)
- Max 3 verify-fix cycles, then escalate
- Per-layer error reporting

### TASK-005 [NEXT] — ONBOARD: discover quests from codebase + data
The system SHOULD mine the installation for quest candidates.
- Codebase TODOs in Odoo modules → latent automation opportunities
- Data quality problems → monitoring quests
- Recurring support tickets → automation candidates
- Manual reports run frequently → quest candidates
- Agent memory files (CLAUDE.md, AGENTS.md) → explicit rules
- High signal = repeated patterns × severity × automation feasibility

### TASK-006 [NEXT] — RECIPE-TEXT as API contract
Procedural instructions MUST live in recipe files, not embedded in agent context.
- `recipes/quest_create.md`, `recipes/quest_improve.md`, etc.
- Recipes are the single authoritative source
- Recipes updatable independently of agent releases
- Recipes use `%(VERSION)s` and `%(INPUT_SCHEMA)s` placeholders

### TASK-007 [NOW] — CONSENT-GATES: confirm before acting
The system MUST require explicit confirmation for significant actions.
- Destructive operations → confirmation required
- API-call-costing operations → confirmation required
- Escalation from local → remote → confirmation required
- Marking as "complete" → only with explicit user OK
- `--anonymous` mode → run locally, no API cost

### TASK-008 [NEXT] — THIN ROUTER SKILL
Skill files MUST be thin routers, not full of embedded procedures.
- Skill body: trigger description + topic table
- "User wants X → fetch recipe Y"
- No embedded procedures in the skill file
- All procedures in `recipes/*.md`

---

## Summary: Scope by Phase

```
┌──────────────────────────────────────────────────────────────────────────┐
│                         SCOPE PER CHANGE                                 │
│                                                                          │
│  THIS CHANGE (ai-agent-core-loop)                                        │
│  ────────────────────────────────                                        │
│  BUZZ-001  Agent while-loop          PROV-001  Provider ABC              │
│  BUZZ-002  Provider as enum          PROV-002  BifrostProvider           │
│  BUZZ-004  Auto-summarize            PROV-003  DirectProvider            │
│  BUZZ-005  Isolated sessions         PROV-004  Provider selection        │
│  BUZZ-006  Timeout + degrade         PROV-005  Model catalog sync        │
│  BUZZ-007  Parallel tools            PROV-006  Retry + error handling     │
│  BUZZ-008  Cancel mid-turn           SESS-001  Session model             │
│  PAPER-007 Activity log              SESS-002  Session lifecycle         │
│  PAPER-008 Plugin system             SESS-003  History persistence       │
│  TASK-007  Consent-gates             SESS-004  Tool binding              │
│  HITL-001  InterruptHandler ABC      HITL-004  AutoInterruptHandler      │
│  HITL-002  DiscussInterruptHandler   HITL-005  Approval threshold        │
│  HITL-003  WebUIInterruptHandler     HITL-006  Mid-turn steering         │
│  ID-001    ai.identity model         ID-005    System prompt compilation  │
│  ID-002    Soul definition           ID-008    Identity templates         │
│  ID-004    Identity scope            GRAPH-001 pgGraph backend           │
│                                      GRAPH-002 graph_query tool          │
│                                      GRAPH-003 Odoo access rights         │
│                                      GRAPH-004 Per-user tenant isolation  │
│                                      GRAPH-005 Email graph (personal)     │
│                                      GRAPH-006 Model registration        │
│                                      TOOL-001 Tool abstraction           │
│                                      TOOL-002 Tool types                 │
│                                      TOOL-003 Tool registration          │
│                                      TOOL-004 Tool execution             │
│                                      TOOL-005 Tool serialization         │
│                                      TOOL-006 Risk levels                │
│  SHARE-001 Unified recipe library   SHARE-005 Pi-agent admin in Odoo    │
│  SHARE-002 Thin router skills       SHARE-006 Skill assignment UI        │
│  SHARE-004 Agent admin in Odoo      SHARE-008 Agent type model          │
│                                                                          │
│  NEXT CHANGE (ai-agent-skills)                                           │
│  ─────────────────────────────                                           │
│  PAPER-002 Skills Manager           TASK-006  Recipe-text               │
│  PAPER-004 Budgets                  TASK-008  Thin router skills         │
│  PAPER-006 Evals & feedback         ID-003    User model                │
│  BUZZ-003  Tools via MCP            ID-006    Identity learning (/learn) │
│  TASK-001  DETECT                   ID-007    Memory integration         │
│  TASK-002  ROUTE                                                        │
│  TASK-003  IMPROVE                                                       │
│  TASK-004  VERIFY                                                        │
│  TASK-005  ONBOARD                                                       │
│                                                                          │
│  NEXT CHANGE (ai-agent-orchestration)                                    │
│  ──────────────────────────────────                                      │
│  HITL-007  Clarification requests    HITL-009  Intercom (parent-child)   │
│  HITL-008  Approval gates            HITL-010  Review loop              │
│                                                                          │
│  FUTURE                                                                  │
│  ──────                                                                  │
│  PAPER-001 BYO-Agent adapter        PAPER-003 Heartbeat lifecycle       │
│  BUZZ-009  Mid-turn steering (extended)                                  │
└──────────────────────────────────────────────────────────────────────────┘
```
