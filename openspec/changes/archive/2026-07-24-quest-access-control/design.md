# Design: Quest Access Control

## Context

The `ai.quest` model currently has a `user_id` (owner) and `company_id` (multi-company) but no mechanism to restrict which users can access a quest via the web chat. The `/ai/chat` endpoint lists all active quests. The `/ai/stream` endpoint accepts any `quest_id` without validating access. This means:

- Internal-only quests (e.g., "HR Onboarding") appear publicly
- Administrative quests (e.g., "Database Cleanup") clutter the user-facing chat
- Any user can invoke any quest by guessing its ID

The `ai_agent_core` module already has a standalone chat UI (`controllers/stream.py`) with SSE streaming. This change adds access control to that UI without touching the underlying agent loop or provider layer.

## Goals / Non-Goals

**Goals:**
- Let quest owners mark a quest as available (or not) in the web chat
- Let quest owners restrict quest access to specific security groups
- Let quest owners restrict quest access to specific users
- Enforce these controls in both `/ai/chat` (listing) and `/ai/stream` (execution)

**Non-Goals:**
- Row-level security via Odoo `ir.rule` — field-level, not database-level
- Discuss channel access — channels have their own membership model, unchanged
- API authentication (API keys, OAuth) — out of scope
- Per-quest rate limiting — out of scope

## Decisions

### D1 — Field-level access control, not `ir.rule`

**Decision:** Use `show_in_chat`, `group_ids`, and `user_ids` fields on `ai.quest` with explicit checks in the controllers, rather than Odoo `ir.rule`.

**Rationale:**
- `ir.rule` would affect ALL Odoo views and ORM access — too broad
- We only want to control chat UI access, not model-level CRUD
- Simpler to implement, easier to debug
- `group_ids` and `user_ids` use standard Odoo Many2many fields

**Alternative considered:** `ir.rule` with domain `[('group_ids', 'in', user.groups_id.ids)]`. Rejected because it would break admin views and internal quest execution (server actions, cron jobs).

### D2 — Three-tier access model

**Decision:** Access is determined by three fields:

| Field | Type | Default | Effect |
|-------|------|---------|--------|
| `show_in_chat` | Boolean | `True` | If `False`, quest never appears in chat regardless of groups/users |
| `group_ids` | Many2many(res.groups) | Empty | If set, ONLY users in these groups can access. Empty = all groups allowed. |
| `user_ids` | Many2many(res.users) | Empty | If set, ONLY these users can access. Empty = all users allowed. |

**Resolution logic:**
1. If `show_in_chat == False` → HIDDEN (no further checks)
2. If `group_ids` is set AND user not in any of them → HIDDEN
3. If `user_ids` is set AND user not in list → HIDDEN
4. Otherwise → VISIBLE

**Rationale:** Simple additive model. Owner + any matching group/user = access. Both `group_ids` and `user_ids` empty = open to all (backward compatible).

### D3 — Controller-level enforcement, not model-level

**Decision:** Add access checks in `stream.py` controllers rather than in the `ai.quest` model.

**Rationale:**
- Chat UI is one of many ways to interact with quests (server actions, cron, discuss)
- Model-level enforcement would block legitimate non-chat uses
- Controllers are the right boundary for UI access control

### D4 — Migration preserves backward compatibility

**Decision:** Existing quests get `show_in_chat=True` and empty `group_ids`/`user_ids` → no behavior change.

**Rationale:** Zero-downtime migration. Users can opt into restrictions by editing quests.

```
┌────────────────────────────────────────────────────────────┐
│                    ACCESS FLOW                             │
│                                                            │
│  User opens /ai/chat                                       │
│       │                                                    │
│       ▼                                                    │
│  For each quest:                                           │
│       │                                                    │
│       ├─ show_in_chat == False? ──→ SKIP (hidden)         │
│       │                                                    │
│       ├─ group_ids set AND user not in any? ──→ SKIP      │
│       │                                                    │
│       ├─ user_ids set AND user not in list? ──→ SKIP      │
│       │                                                    │
│       └─→ SHOW in chat list                                │
│                                                            │
│  User selects quest → /ai/stream?quest_id=X                │
│       │                                                    │
│       ├─ show_in_chat == False? ──→ 403 Forbidden         │
│       ├─ group access check fails? ──→ 403 Forbidden       │
│       ├─ user access check fails? ──→ 403 Forbidden        │
│       └─→ Stream response OK                               │
└────────────────────────────────────────────────────────────┘
```

## Risks / Trade-offs

- **[Access check duplication]**: Same logic in `/ai/chat` and `/ai/stream` → Mitigation: Extract to shared helper function `_quest_is_accessible(quest, user)`.
- **[Admin override needed]**: Admin users should be able to access all quests regardless of restrictions → Mitigation: Add `user.has_group('base.group_system')` bypass.
- **[Many2many on high-volume model]**: `user_ids` could grow large if quest is shared with many users → Mitigation: Recommend using `group_ids` for broad access and `user_ids` only for exceptions.
- **[No API-level enforcement]**: If someone calls `/ai/stream` directly with a quest ID, access is checked → Mitigation: Already handled in D3.

## Open Questions

- Should `show_in_chat=False` also hide the quest from discuss channel auto-responses? → Current: No, discuss channels have their own membership model.
- Should quest `user_id` (owner) always have access regardless of `group_ids`/`user_ids`? → Current: Yes, implicit owner access. Documented in spec.
