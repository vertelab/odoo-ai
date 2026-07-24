# Spec: Chat UI Filtering

The `/ai/chat` and `/ai/stream` endpoints SHALL enforce quest visibility and access controls.

## ADDED Requirements

### Requirement: Chat endpoint filters by access
The `/ai/chat` endpoint SHALL filter the quest list to only include quests where `show_in_chat=True` AND the current user has access (matching `group_ids`, `user_ids`, or is the owner, or is admin).

#### Scenario: Chat list respects all filters
- **WHEN** a user opens `/ai/chat`
- **THEN** the quest list SHALL only include quests passing the access check
- **AND** the "Allmän assistent" (default quest with no ID) SHALL always be present

#### Scenario: Default quest always shown
- **WHEN** any user opens `/ai/chat`
- **THEN** the "Allmän assistent" placeholder (no quest_id) SHALL be visible regardless of access restrictions

### Requirement: Stream endpoint validates access
The `/ai/stream` endpoint SHALL validate quest access before executing. If the quest is not accessible to the current user, the endpoint SHALL return HTTP 403 with a JSON error message.

#### Scenario: Stream rejects unauthorized quest
- **WHEN** a user requests `/ai/stream?quest_id=X`
- **AND** quest X is not accessible to the user
- **THEN** the server SHALL return `{"error": "Quest not accessible"}`
- **AND** the HTTP status SHALL be 403

#### Scenario: Stream accepts default (no quest_id)
- **WHEN** a user requests `/ai/stream` without a `quest_id`
- **THEN** the server SHALL process the request using default configuration
- **AND** no access check SHALL be performed

### Requirement: Access check helper is reusable
The access control logic SHALL be implemented as a standalone function `_quest_is_accessible(quest, user)` so both `/ai/chat` and `/ai/stream` use the same logic. This prevents divergence and simplifies testing.

#### Scenario: Same logic for both endpoints
- **WHEN** `_quest_is_accessible(quest, user)` returns `False`
- **THEN** both `/ai/chat` filtering and `/ai/stream` validation SHALL reject the quest

### Requirement: Non-authenticated users get public-only
For public (non-authenticated) users accessing `/ai/chat`, only quests with `group_ids=[]`, `user_ids=[]`, and `show_in_chat=True` SHALL be visible. Quests with any access restrictions SHALL be hidden from public users.

#### Scenario: Public user sees only open quests
- **WHEN** a non-authenticated user opens `/ai/chat`
- **THEN** only quests with no group or user restrictions SHALL be visible
- **AND** quests with `show_in_chat=False` MUST be hidden
