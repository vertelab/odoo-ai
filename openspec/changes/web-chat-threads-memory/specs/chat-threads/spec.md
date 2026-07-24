# Spec: Chat Threads

The web chat SHALL organize conversations into named, persistent threads.

## ADDED Requirements

### Requirement: Thread creation
The system SHALL automatically create a new thread when a user sends their first message without an active thread. The thread name SHALL be auto-generated from the first 60 characters of the first message.

#### Scenario: Auto-create thread on first message
- **WHEN** a user sends a message "Analyze Q2 sales performance across all regions"
- **AND** no active thread exists
- **THEN** a new `ai.quest.session` SHALL be created
- **AND** its `thread_name` SHALL be "Analyze Q2 sales performance across all regions"

### Requirement: Thread list in sidebar
The sidebar SHALL display a list of the user's threads, ordered by most recent activity. Each thread SHALL show its name and relative timestamp.

#### Scenario: Thread list shows user's threads
- **WHEN** a user opens `/ai/chat`
- **THEN** the sidebar SHALL list all threads belonging to that user
- **AND** threads SHALL be ordered by last activity (newest first)

#### Scenario: Click thread loads history
- **WHEN** a user clicks a thread in the sidebar
- **THEN** the thread's message history SHALL be loaded and displayed
- **AND** the input area SHALL be ready for new messages

### Requirement: Thread renaming
The user SHALL be able to rename a thread. Renaming SHALL update the `thread_name` field on the session record.

#### Scenario: Rename thread via UI
- **WHEN** a user double-clicks a thread name in the sidebar
- **THEN** the name SHALL become editable
- **AND** pressing Enter SHALL save the new name

### Requirement: Thread deletion
The user SHALL be able to delete a thread. Deletion SHALL mark the session as inactive (soft delete) rather than destroying data.

#### Scenario: Delete thread from sidebar
- **WHEN** a user clicks the delete button on a thread
- **AND** confirms the deletion
- **THEN** the thread SHALL be removed from the sidebar
- **AND** the session's `active` field SHALL be set to False

### Requirement: New thread button
The sidebar SHALL include a "+" or "New Thread" button. Clicking it SHALL clear the current conversation and prepare for a new thread.

#### Scenario: New thread clears chat
- **WHEN** a user clicks "New Thread"
- **THEN** the chat area SHALL clear
- **AND** no thread SHALL be active until the first message is sent

### Requirement: Thread API endpoints
The system SHALL provide REST endpoints for thread management:
- `GET /ai/threads` — list user's threads
- `POST /ai/threads` — create new thread (returns session_id)
- `PUT /ai/threads/{id}` — rename thread
- `DELETE /ai/threads/{id}` — delete (archive) thread

#### Scenario: API returns thread list
- **WHEN** a GET request is made to `/ai/threads`
- **THEN** the response SHALL be a JSON array of threads with id, name, last_activity
- **AND** threads SHALL be scoped to the authenticated user

### Requirement: Thread access follows quest access
Thread access SHALL respect the quest's access control (`show_in_chat`, `group_ids`, `user_ids`). Users SHALL only see threads for quests they can access.

#### Scenario: Thread hidden if quest is restricted
- **WHEN** a user requests their thread list
- **AND** a thread belongs to a quest with `group_ids` not matching the user
- **THEN** that thread SHALL NOT appear in the list
