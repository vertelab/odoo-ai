# Spec: Quest Access Control

Restricts quest access by user groups and individual users in the web chat.

## ADDED Requirements

### Requirement: Group-based access control
The `ai.quest` model SHALL have a `group_ids` field (Many2many to `res.groups`). When set, ONLY users belonging to at least one of the specified groups SHALL be able to see and execute the quest via `/ai/chat` and `/ai/stream`.

#### Scenario: User in allowed group sees quest
- **WHEN** a user opens `/ai/chat`
- **AND** a quest has `group_ids = [HR Manager]`
- **AND** the user belongs to the "HR Manager" group
- **THEN** the quest SHALL appear in the user's quest list

#### Scenario: User not in allowed group cannot see quest
- **WHEN** a user opens `/ai/chat`
- **AND** a quest has `group_ids = [HR Manager]`
- **AND** the user does NOT belong to "HR Manager"
- **THEN** the quest MUST NOT appear in the user's quest list

#### Scenario: User not in group rejected on stream
- **WHEN** a user requests `/ai/stream?quest_id=X`
- **AND** quest X has `group_ids = [HR Manager]`
- **AND** the user does NOT belong to "HR Manager"
- **THEN** the server SHALL return HTTP 403

### Requirement: User-based access control
The `ai.quest` model SHALL have a `user_ids` field (Many2many to `res.users`). When set, ONLY the specified users SHALL be able to see and execute the quest via `/ai/chat` and `/ai/stream`.

#### Scenario: Specified user sees quest
- **WHEN** user Alice opens `/ai/chat`
- **AND** a quest has `user_ids = [Alice, Bob]`
- **THEN** Alice SHALL see the quest in her list

#### Scenario: Non-specified user cannot see quest
- **WHEN** user Carol opens `/ai/chat`
- **AND** a quest has `user_ids = [Alice, Bob]`
- **THEN** Carol MUST NOT see the quest

### Requirement: Empty groups and users means open access
When both `group_ids` and `user_ids` are empty, the quest SHALL be accessible to all authenticated users (subject to `show_in_chat`). This is the default for backward compatibility.

#### Scenario: Empty restrictions = open access
- **WHEN** a user opens `/ai/chat`
- **AND** a quest has `group_ids = []` and `user_ids = []`
- **AND** `show_in_chat = True`
- **THEN** the quest SHALL appear for all users

### Requirement: Combined group and user access
When BOTH `group_ids` and `user_ids` are set, a user qualifies for access if they match EITHER condition (OR logic). The user does NOT need to match both.

#### Scenario: User matches group but not user list
- **WHEN** user Alice opens `/ai/chat`
- **AND** a quest has `group_ids = [HR Manager]` and `user_ids = [Bob]`
- **AND** Alice belongs to "HR Manager" but is not in `user_ids`
- **THEN** Alice SHALL see the quest

### Requirement: Quest owner always has access
The `user_id` (owner) of a quest SHALL always have access regardless of `group_ids` or `user_ids` restrictions. This ensures the creator can always manage their own quests.

#### Scenario: Owner accesses restricted quest
- **WHEN** user Alice (quest owner) opens `/ai/chat`
- **AND** a quest owned by Alice has `group_ids = [Finance]` and Alice is NOT in Finance
- **THEN** Alice SHALL still see and execute the quest

### Requirement: Admin bypass for access control
Users with `base.group_system` (Administration / Settings) SHALL bypass all group and user access restrictions. This ensures administrators can manage all quests.

#### Scenario: Admin bypasses all restrictions
- **WHEN** an admin user accesses any quest
- **AND** the quest has restrictive `group_ids` and `user_ids` not matching the admin
- **THEN** the admin SHALL have unrestricted access
