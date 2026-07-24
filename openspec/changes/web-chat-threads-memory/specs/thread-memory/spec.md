# Spec: Thread Memory

The web chat SHALL persist message history per thread and restore it on subsequent visits.

## ADDED Requirements

### Requirement: Message persistence as session lines
Every user message and assistant response SHALL be persisted as `ai.quest.session.line` records. Lines SHALL include role, content, sequence number, and token counts.

#### Scenario: User message is persisted
- **WHEN** a user sends a message in a thread
- **THEN** a session line with `role = 'user'` SHALL be created
- **AND** the line SHALL be assigned the next sequence number

#### Scenario: Assistant response is persisted
- **WHEN** the agent completes its response
- **THEN** a session line with `role = 'assistant'` SHALL be created
- **AND** token counts SHALL be recorded

#### Scenario: Tool results are persisted
- **WHEN** the agent executes a tool during a response
- **THEN** a session line with `role = 'tool'` SHALL be created
- **AND** the tool name and result SHALL be stored

### Requirement: History loading on thread switch
When a user clicks a thread, its complete message history SHALL be loaded from session lines and displayed. The input area SHALL be ready for the next message.

#### Scenario: Load thread with 10 messages
- **WHEN** a user clicks a thread that has 10 session lines
- **THEN** all 10 messages SHALL be displayed in order
- **AND** the chat SHALL scroll to the bottom

### Requirement: Auto-summarize long threads
When a thread exceeds 50 messages, the system SHALL auto-summarize earlier messages into a system-level context note. This SHALL use the existing `_summarize` method in AgentLoop.

#### Scenario: Long thread gets summarized
- **WHEN** a thread has 60 messages
- **AND** the user sends a new message
- **THEN** messages 1-50 SHALL be summarized into a system context
- **AND** messages 51-60 SHALL remain as individual lines

### Requirement: Thread history sent to AgentLoop
The `/ai/stream` endpoint SHALL reconstruct the message history from session lines and pass it to the AgentLoop along with the new prompt.

#### Scenario: AgentLoop receives full history
- **WHEN** `/ai/stream` is called with `session_id=42`
- **AND** the thread has 5 previous messages
- **THEN** the AgentLoop SHALL receive those 5 messages as history
- **AND** the new prompt SHALL be appended

### Requirement: Session line model
The system SHALL have an `ai.quest.session.line` model with fields: `session_id` (Many2one), `sequence` (Integer), `role` (Selection: user/assistant/tool/system), `content` (Text), `tool_calls` (Text, JSON), `token_input` (Integer), `token_output` (Integer), `create_date` (Datetime).

#### Scenario: Model is registered and accessible
- **WHEN** the module is installed
- **THEN** `ai.quest.session.line` SHALL be queryable via Odoo ORM
- **AND** access rights SHALL be configured for authenticated users
