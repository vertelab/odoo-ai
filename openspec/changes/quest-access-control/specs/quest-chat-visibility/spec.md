# Spec: Quest Chat Visibility

Controls whether a quest appears in the `/ai/chat` web UI.

## ADDED Requirements

### Requirement: Quest chat visibility flag
The `ai.quest` model SHALL have a `show_in_chat` boolean field defaulting to `True`. When `False`, the quest MUST NOT appear in the `/ai/chat` web UI quest list and MUST NOT be executable via `/ai/stream` from the web chat.

#### Scenario: Hidden quest not shown in chat list
- **WHEN** a user opens `/ai/chat`
- **AND** a quest has `show_in_chat = False`
- **THEN** the quest MUST NOT appear in the sidebar quest list

#### Scenario: Hidden quest rejected on stream
- **WHEN** a user requests `/ai/stream?quest_id=X`
- **AND** quest X has `show_in_chat = False`
- **THEN** the server SHALL return HTTP 403 with an error message

#### Scenario: Visible quest appears in chat
- **WHEN** a user opens `/ai/chat`
- **AND** a quest has `show_in_chat = True`
- **THEN** the quest SHALL appear in the sidebar (subject to access control)

### Requirement: Existing quests default to visible
All existing `ai.quest` records SHALL default to `show_in_chat = True` to preserve current behavior. This ensures zero-downtime migration.

#### Scenario: Migration preserves visibility
- **WHEN** the module is upgraded
- **AND** existing quests have no `show_in_chat` value
- **THEN** all existing quests SHALL have `show_in_chat = True`

### Requirement: Admin bypass for hidden quests
Users with `base.group_system` (Administration / Settings) SHALL be able to see and execute all quests regardless of `show_in_chat` value. This ensures administrators can test and debug hidden quests.

#### Scenario: Admin sees hidden quest
- **WHEN** an admin user opens `/ai/chat`
- **AND** a quest has `show_in_chat = False`
- **THEN** the quest SHALL appear in the admin's quest list
