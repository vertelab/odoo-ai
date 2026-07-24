# Spec: Quest Learning Memory

The system SHALL extract key facts from conversations and use them to improve future responses.

## ADDED Requirements

### Requirement: Active memory extraction per thread
After each assistant response, the system SHALL perform a lightweight extraction of 1-3 key facts from the exchange. Facts SHALL be stored as `ai.memory` records linked to the quest.

#### Scenario: Fact extracted from conversation
- **WHEN** a user tells the agent "I prefer responses in Swedish"
- **AND** the agent responds
- **THEN** a memory record SHALL be created with fact: "User prefers Swedish"
- **AND** the memory SHALL have category "preference" and importance "high"

#### Scenario: Fact extraction uses cheap model
- **WHEN** memory extraction runs
- **THEN** it SHALL use the cheapest available model (not the main conversation model)
- **AND** extraction SHALL complete within 3 seconds

### Requirement: Daily IMPROVE consolidation
An Odoo cron job SHALL run daily to consolidate thread memories into the quest's system prompt. The cron SHALL group memories by category, de-duplicate, rank by importance, and update `ai.identity.user_model`.

#### Scenario: Memories consolidated into identity
- **WHEN** the daily cron runs for a quest with 15 memories
- **THEN** the quest's `identity_id.user_model` SHALL be updated with consolidated text
- **AND** duplicate facts SHALL be merged

### Requirement: Memory injection into system prompt
The quest's memories SHALL be injected into the system prompt for every conversation. The injection SHALL happen during `ai.identity._compute_system_prompt()` which already compiles soul + user_model + skills.

#### Scenario: System prompt includes memories
- **WHEN** a user starts a new thread with a quest that has memories
- **THEN** the system prompt SHALL include a "Learned about this quest" section
- **AND** the section SHALL list consolidated memories

### Requirement: Memory ranking and capping
Memories SHALL be ranked by importance (high/medium/low) and recency. The system SHALL cap the number of active memories per quest at 50. Low-importance memories older than 30 days SHALL be auto-archived.

#### Scenario: Memory cap enforced
- **WHEN** a quest has 55 active memories
- **THEN** the 5 lowest-ranked memories SHALL be archived
- **AND** only 50 SHALL remain active

### Requirement: Memory extraction respects quest access
Memory extraction SHALL only run for threads belonging to quests the user can access. No memory SHALL be extracted from restricted quests for unauthorized users.

#### Scenario: No extraction on restricted quest
- **WHEN** a user interacts with a quest
- **AND** the user does not have access to that quest
- **THEN** no memory extraction SHALL occur

### Requirement: Memory model extension
The `ai.memory` model SHALL be extended with: `quest_id` (Many2one to ai.quest), `category` (Selection: preference/fact/correction/pattern), `importance` (Selection: high/medium/low), `source_thread_id` (Many2one to ai.quest.session), `consolidated` (Boolean), `archived` (Boolean).

#### Scenario: Memory linked to quest and thread
- **WHEN** a memory is extracted from a thread
- **THEN** the memory SHALL reference both the quest and the source thread
