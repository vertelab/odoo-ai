# Proposal: Quest Access Control — Chat Visibility & User/Group Permissions

## Why

Currently, all active `ai.quest` records appear in the web chat UI (`/ai/chat`) regardless of intended audience. There is no way to restrict a quest to specific users, groups, or hide it from the web chat entirely. This creates privacy issues (internal-only quests exposed publicly) and clutter (administrative quests visible to end users).

## What Changes

- **New field `show_in_chat`** on `ai.quest` — boolean controlling whether the quest appears in the `/ai/chat` web UI
- **New field `group_ids`** on `ai.quest` — Many2many to `res.groups`, restricting which security groups can access the quest
- **New field `user_ids`** on `ai.quest` — Many2many to `res.users`, restricting which specific users can access the quest
- **Access enforcement in `/ai/chat`** — Filter quest list by `show_in_chat=True` AND user's groups/users
- **Access enforcement in `/ai/stream`** — Reject requests to restricted quests with 403
- **Default migration** — Existing quests get `show_in_chat=True` and no group/user restrictions (preserving current behavior)

## Capabilities

### New Capabilities
- `quest-chat-visibility`: Controls whether a quest is listed in the web chat UI
- `quest-access-control`: Restricts quest access by user groups and/or individual users
- `chat-ui-filtering`: The `/ai/chat` endpoint respects visibility and access controls when listing quests

### Modified Capabilities
<!-- No existing spec needs requirement-level changes — these are new features on an existing model -->

## Impact

- **`models/ai_quest.py`** — New fields: `show_in_chat`, `group_ids`, `user_ids`
- **`controllers/stream.py`** — Modify `/ai/chat` quest listing and `/ai/stream` access check
- **`views/ai_quest_views.xml`** — Add new fields to form view
- **`security/ir.model.access.csv`** — No changes needed (fields follow existing CRUD permissions)
- **`tests/test_core.py`** — Add test for new fields and filtering logic
- No breaking changes — existing behavior preserved via defaults

## Non-goals

- NOT implementing row-level security (RLS) via `ir.rule` — this is field-level access control, not database-level
- NOT changing discuss channel behavior — channels already have their own membership model
- NOT adding OAuth2/API-key authentication for chat — out of scope
- NOT building a permission UI outside the standard Odoo form view
