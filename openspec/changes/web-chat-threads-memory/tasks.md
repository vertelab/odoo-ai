# Tasks: Web Chat Threads & Memory

## 1. Frontend: Theme Toggle

- [ ] 1.1 Define light theme CSS custom properties under `[data-theme="light"]`
- [ ] 1.2 Add theme toggle button (🌗) to chat header
- [ ] 1.3 Implement JS toggle logic with `document.documentElement.dataset.theme`
- [ ] 1.4 Persist theme preference to localStorage (`ai-chat-theme`)
- [ ] 1.5 Detect system preference on first visit (`prefers-color-scheme`)
- **Depends on:** nothing

## 2. Frontend: Responsive Design

- [ ] 2.1 Add `@media (max-width: 768px)` block with mobile layout rules
- [ ] 2.2 Implement collapsible sidebar with slide-over animation
- [ ] 2.3 Add hamburger menu button (≡) visible only on mobile
- [ ] 2.4 Add overlay behind sidebar when open on mobile
- [ ] 2.5 Enlarge touch targets (inputs, buttons) to min 44px on mobile
- **Depends on:** nothing

## 3. Model: Session Lines

- [ ] 3.1 Create `ai.quest.session.line` model with fields: session_id, sequence, role, content, tool_calls, token_input, token_output
- [ ] 3.2 Add `thread_name` Char field to `ai.quest.session`
- [ ] 3.3 Add `session_line_ids` One2many to `ai.quest.session`
- [ ] 3.4 Add access rights in `security/ir.model.access.csv`
- [ ] 3.5 Register model in `models/__init__.py`
- **Depends on:** nothing

## 4. Model: Memory Extension

- [ ] 4.1 Add `quest_id`, `category`, `importance`, `source_thread_id`, `consolidated`, `archived` fields to `ai.memory`
- [ ] 4.2 Add `_compute_system_prompt` extension to include active memories
- **Depends on:** 3.1

## 5. API: Thread CRUD

- [ ] 5.1 Implement `GET /ai/threads` — list user's threads
- [ ] 5.2 Implement `POST /ai/threads` — create new thread (returns session_id)
- [ ] 5.3 Implement `PUT /ai/threads/{id}` — rename thread
- [ ] 5.4 Implement `DELETE /ai/threads/{id}` — soft-delete thread
- [ ] 5.5 Apply quest access control to thread endpoints
- **Depends on:** 3.2, 3.3

## 6. API: Thread Search

- [ ] 6.1 Implement `GET /ai/thread/search?q=` — full-text search via ilike
- [ ] 6.2 Return matching threads with highlighted snippets
- [ ] 6.3 Scope results to user's accessible quests
- **Depends on:** 3.1, 5.1

## 7. API: Stream with History

- [ ] 7.1 Accept `session_id` parameter in `/ai/stream`
- [ ] 7.2 Load thread history from `session_line_ids`
- [ ] 7.3 Inject quest memories into system prompt
- [ ] 7.4 Save user message as session line before streaming
- [ ] 7.5 Save assistant response as session line after streaming
- [ ] 7.6 Auto-summarize threads > 50 messages before sending to AgentLoop
- **Depends on:** 3.1, 4.2

## 8. Memory Extraction

- [ ] 8.1 Implement active extraction: LLM call after each response to extract key facts
- [ ] 8.2 Store extracted facts as `ai.memory` linked to quest and thread
- [ ] 8.3 Use cheapest available model for extraction (cerebras/gpt-oss-120b)
- [ ] 8.4 Run extraction asynchronously (don't block response streaming)
- **Depends on:** 4.1, 7.5

## 9. Daily IMPROVE Consolidation

- [ ] 9.1 Create `ai.quest.memory.consolidate()` method
- [ ] 9.2 Group memories by category, de-duplicate, rank by importance
- [ ] 9.3 Update `identity_id.user_model` with consolidated text
- [ ] 9.4 Create Odoo cron job for daily consolidation
- [ ] 9.5 Auto-archive low-importance memories > 30 days
- **Depends on:** 4.1, 8.1

## 10. Frontend: Thread UI

- [ ] 10.1 Add thread list to sidebar (loaded from `/ai/threads`)
- [ ] 10.2 Add "New Thread" button in sidebar header
- [ ] 10.3 Implement thread click: clear chat, load history, activate thread
- [ ] 10.4 Implement double-click rename on thread name
- [ ] 10.5 Implement delete button on thread with confirmation
- [ ] 10.6 Auto-create thread on first message if none active
- [ ] 10.7 Add thread search input above thread list
- [ ] 10.8 Wire search input to `/ai/thread/search` endpoint
- [ ] 10.9 Show relative timestamps on thread list items
- **Depends on:** 5.1, 5.2, 6.1

## 11. Testing

- [ ] 11.1 Test theme toggle: dark → light → dark, localStorage persistence
- [ ] 11.2 Test responsive: mobile sidebar collapse, hamburger, overlay
- [ ] 11.3 Test thread CRUD: create, rename, delete
- [ ] 11.4 Test thread search: ilike matching, access filtering
- [ ] 11.5 Test history loading: messages restored on thread switch
- [ ] 11.6 Test memory extraction: facts stored after conversation
- [ ] 11.7 Test daily consolidation: memories merged into identity
- [ ] 11.8 Test system prompt injection: memories appear in prompt
- [ ] 11.9 Run existing tests — verify no regressions
- **Depends on:** all above
