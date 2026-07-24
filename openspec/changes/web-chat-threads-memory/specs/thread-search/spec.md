# Spec: Thread Search

The web chat SHALL support full-text search across thread messages.

## ADDED Requirements

### Requirement: Search input in sidebar
The sidebar SHALL include a search input field. As the user types, matching threads SHALL be filtered in real-time.

#### Scenario: Search filters thread list
- **WHEN** a user types "sales" in the search input
- **THEN** only threads containing "sales" in their messages SHALL be displayed
- **AND** threads without matches SHALL be hidden

#### Scenario: Empty search shows all threads
- **WHEN** a user clears the search input
- **THEN** all threads SHALL be displayed again

### Requirement: Search endpoint
The system SHALL provide a `GET /ai/thread/search?q=<query>` endpoint that returns matching threads with highlighted snippets.

#### Scenario: Search returns matching threads
- **WHEN** a GET request is made to `/ai/thread/search?q=sales`
- **THEN** the response SHALL be a JSON array of threads
- **AND** each result SHALL include the thread id, name, and a snippet of the matching message

### Requirement: Search uses ilike matching
Search SHALL use Odoo's `ilike` domain operator on `ai.quest.session.line` content field. Results SHALL be scoped to the authenticated user's threads.

#### Scenario: Case-insensitive search
- **WHEN** a user searches for "SALES"
- **THEN** messages containing "sales", "Sales", or "SALES" SHALL all match

### Requirement: Search respects quest access
Search results SHALL exclude threads belonging to quests the user cannot access.

#### Scenario: Restricted quest threads excluded from search
- **WHEN** a user searches across all threads
- **AND** some threads belong to restricted quests
- **THEN** threads from restricted quests SHALL NOT appear in search results
