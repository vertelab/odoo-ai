# Change 3: Agent Identity, Skills & Graph Data

## Summary

Add the complete agent identity system (SOUL.md), shared skills library, graph data integration, and the full Taskless learning layer. This is where agents become "personal companions" with persistent identity and continuous improvement.

## In scope

### Identity (8 requirements)
- **ID-001..008**: ai.identity model — SOUL + user_model + memory + skills → compiled system_prompt. Identity templates, scope (personal/org/public).

### Skills (8 requirements)
- **SHARE-001..008**: Unified recipe library, thin router skills, agent administration in Odoo, skill assignment UI, agent type model.

### Graph Data (6 requirements)
- **GRAPH-001..006**: pgGraph backend, graph_query tool, Odoo access rights, tenant isolation, email graph, model registration.

### Tool System (extended)
- **TOOL-002..003**: OdooModelTools auto-generation, MCPTools discovery, CustomTools.

### Provider (extended)
- **PROV-005**: Model catalog sync from Bifrost.

### Taskless Layer (9 requirements)
- **TASK-001..008**: DETECT, ROUTE, IMPROVE, VERIFY, ONBOARD, recipe-text, consent-gates, thin router skills.

### Kaizen Loop (1 requirement)
- **KAIZEN-001**: Weekly self-review. Agent wakes on schedule, analyzes its own performance (error rates, costs, patterns), proposes improvements with evidence, implements human-approved changes, and tracks metrics week-over-week.

### Web UI (admin + power user)
- **UI-020**: Agent configuration panel — edit skills, tools, model, budget per agent from chat UI.
- **UI-021**: Start/stop agents — control agent lifecycle from admin panel.
- **UI-022**: Activity log — scrollable audit trail showing all agent actions with timestamps.
- **UI-023**: State inspector — debug view showing raw agent state, messages, tool calls.
- **UI-024**: Chat history search — full-text search across all past sessions.
- **UI-025**: Theme toggle — dark/light mode.
- **UI-026**: Budget overview — monthly spend vs limit with progress bar, per-agent breakdown. Bridges PAPER-006 (Evals) and TASK-003 (IMPROVE) — makes improvement proactive, not reactive.

## Depends on
- Change 1 (core loop)
- Change 2 (interrupt)

## Deliverables
- ai.identity model + views
- ai.skill model + admin UI
- pgGraph integration + graph_query tool
- Recipe library migration from ~74 existing skills
- Taskless improve/verify/detect/route/onboard layer

**Estimated: 3-4 weeks**
