# Design: Web Chat Threads & Memory

## Context

The existing `/ai/chat` endpoint serves a single HTML page with inline CSS and vanilla JS. It uses SSE streaming via `/ai/stream` for responses. No state is persisted between page loads. The `ai.quest.session` model exists but is unused by the chat UI — it's only populated by `AIQuestRun` wizard in the Odoo backend.

This change transforms the chat from a stateless demo into a stateful, multi-threaded, self-improving application — while keeping the zero-dependency frontend approach (no React, no build step).

## Goals / Non-Goals

**Goals:**
- Responsive UI that works on phones, tablets, and desktop
- Light/dark theme with localStorage persistence
- Thread CRUD with auto-naming
- Full-text search across threads
- Persistent message history per thread
- Active memory extraction per thread + daily consolidation
- Memory injection into system prompt

**Non-Goals:**
- Real-time collaboration
- File/image upload
- Native mobile app
- pgVector semantic search (future)
- Framework migration (stays vanilla HTML/CSS/JS)

## Decisions

### D1 — Vanilla frontend, no framework

**Decision:** Keep the existing vanilla HTML/CSS/JS approach. No React, Vue, or build step.

**Rationale:**
- Current approach is ~150 lines of JS — manageable without a framework
- SSE streaming is already implemented in vanilla JS
- No npm/yarn dependency, no build pipeline
- Odoo integration is simpler without a SPA
- The complexity ceiling for chat UI is within vanilla JS capabilities

**Alternative considered:** React/Vue SPA with Vite. Rejected because it adds a build step, requires serving static assets, and complicates the Odoo integration.

### D2 — CSS custom properties for theming

**Decision:** Use `[data-theme="dark"]` and `[data-theme="light"]` selectors with CSS custom properties.

```css
:root, [data-theme="dark"] {
    --bg: #1a1a2e;
    --text: #e0e0e0;
    /* ... */
}
[data-theme="light"] {
    --bg: #ffffff;
    --text: #333333;
    /* ... */
}
```

Toggle sets `document.documentElement.dataset.theme` and persists to `localStorage`. On load, check localStorage → fallback to `prefers-color-scheme` → fallback to dark.

### D3 — ai.quest.session as thread

**Decision:** Each thread IS an `ai.quest.session` record. Rename is just updating `thread_name` field. Delete sets `active=False` (soft delete).

Thread lifecycle:
```
[New] → user sends first message → session created, thread_name = first 60 chars of message
     → more messages added as session_line records
     → user can rename via PUT /ai/threads/{id}
     → user can delete via DELETE /ai/threads/{id} (archives, doesn't destroy)
```

### D4 — Session lines for message persistence

**Decision:** New model `ai.quest.session.line` stores individual messages:

| Field | Type | Description |
|-------|------|-------------|
| `session_id` | Many2one | Parent thread |
| `sequence` | Integer | Order within thread |
| `role` | Selection | user / assistant / tool / system |
| `content` | Text | Message body |
| `tool_calls` | Text | JSON: tool call metadata |
| `token_input` | Integer | Tokens consumed by this exchange |
| `token_output` | Integer | Tokens generated |

History is reconstructed on thread load by reading lines ordered by sequence.

### D5 — Active memory extraction (per thread)

**Decision:** After each assistant response, a lightweight LLM call extracts key facts:

```
System: "Extract 1-3 key facts from this exchange. Return JSON: [{fact, category, importance}]"
User: [last exchange]
→ [{fact: "User prefers Swedish", category: "preference", importance: "high"}, ...]
```

Facts are stored as `ai.memory` records linked to the quest. This runs synchronously after the main response (adds ~1s latency per turn for the extraction call — acceptable).

### D6 — Daily IMPROVE consolidation

**Decision:** An Odoo cron job runs daily, consolidating thread memories into a structured system prompt section:

```
Cron: ai.quest.memory.consolidate()
1. Gather all ai.memory for this quest
2. Group by category
3. De-duplicate and rank by importance
4. Update quest.identity_id.user_model with consolidated text
5. The identity's _compute_system_prompt() already includes user_model
```

This uses the existing `ai.identity` model's system prompt compilation — no new mechanism needed.

### D7 — Search via ilike

**Decision:** Full-text search uses Odoo domain `[('content', 'ilike', query)]` on session lines.

**Rationale:** Simple, works immediately, no pgVector dependency. Upgrade path to semantic search is clear: add a `search_semantic()` method later that uses pgVector embeddings.

### D8 — Responsive breakpoints

**Decision:** Single breakpoint at 768px. Below: sidebar becomes slide-over, messages go full-width, inputs get larger touch targets.

```
Desktop (>768px):           Mobile (≤768px):
┌──────┬──────────┐        ┌──────────────────┐
│280px │  Chat    │        │ ≡ AI Chat    🌗  │
│Side- │  area    │        ├──────────────────┤
│bar   │          │        │ Messages         │
│      │          │        │ (full width)     │
└──────┴──────────┘        ├──────────────────┤
                           │ [Input] [Skicka] │
                           └──────────────────┘
                           Sidebar: position:fixed, left:-280px
                           .sidebar.open { left: 0 }
                           Overlay behind sidebar when open
```

## Data Flow

```
┌─────────────────────────────────────────────────────────────┐
│                    REQUEST FLOW                             │
│                                                             │
│  User types message                                         │
│       │                                                     │
│       ▼                                                     │
│  Frontend:                                                  │
│    1. If no active thread → POST /ai/threads (create)       │
│    2. Append message to UI                                  │
│    3. GET /ai/stream?session_id=X&prompt=Y                  │
│       │                                                     │
│       ▼                                                     │
│  Backend /ai/stream:                                        │
│    1. Load thread history from session_line_ids             │
│    2. Inject quest memories into system prompt              │
│    3. Run AgentLoop with history + prompt                   │
│    4. Stream response via SSE                               │
│    5. Save user message + AI response as session_lines      │
│    6. Run memory extraction (D5)                            │
│       │                                                     │
│       ▼                                                     │
│  Daily cron:                                                │
│    1. Consolidate memories per quest (D6)                   │
│    2. Update identity.user_model                            │
│    3. Next conversation includes updated memories           │
└─────────────────────────────────────────────────────────────┘
```

## Risks / Trade-offs

- **[Active extraction adds latency]**: 1 extra LLM call per turn → Mitigation: Use cheapest/fastest model (e.g., cerebras/gpt-oss-120b), run asynchronously after response is sent.
- **[Memory bloat]**: Too many extracted facts → Mitigation: Rank by importance, cap at 50 memories per quest, auto-archive low-importance facts.
- **[Session line table growth]**: Lines grow linearly with usage → Mitigation: Auto-summarize threads > 50 messages, archive old lines.
- **[CSS-only responsive]**: Limited compared to a mobile framework → Mitigation: Acceptable for a chat UI. The complexity is low — layout, not interaction-heavy.

## Open Questions

- Memory extraction model: should it be configurable per quest or hardcoded?
- Thread list pagination: how many threads to show before "load more"?
