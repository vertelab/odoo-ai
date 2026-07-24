# Proposal: Web Chat Threads & Memory

## Why

The current web chat (`/ai/chat`) is a single-session, dark-mode-only, desktop-only experience with no persistence. Conversations vanish on page reload. Users can't organize their work across threads, search past conversations, or benefit from the agent learning across sessions. This limits the chat from being a useful daily tool to a one-off demo.

## What Changes

- **Responsive design** — CSS media queries, collapsible sidebar with hamburger menu, touch-optimized inputs
- **Light/dark theme toggle** — CSS variable swap, localStorage persistence, system preference detection
- **Thread-based conversations** — Create, rename, delete threads. Auto-named from first message. Each thread is an `ai.quest.session`.
- **Full-text thread search** — Search across all messages in user's threads via Odoo `ilike`
- **Thread memory** — Persist all messages as `ai.quest.session.line` records. Load history on thread switch. Auto-summarize long threads via existing AgentLoop.
- **Quest self-learning memory** — Active extraction per thread: agent identifies key facts after each response. Daily IMPROVE cron consolidates learnings into `ai.memory`. Memories injected into system prompt for future conversations.

## Capabilities

### New Capabilities
- `responsive-chat-ui`: Mobile-friendly layout with collapsible sidebar, hamburger menu, touch-optimized controls
- `theme-toggle`: Light/dark mode switch with localStorage persistence and system preference detection
- `chat-threads`: Thread-based conversation management — create, auto-name, rename, delete, list
- `thread-search`: Full-text search across thread messages
- `thread-memory`: Per-thread message persistence via session lines, history loading, auto-summarization
- `quest-learning-memory`: Active fact extraction per thread, daily IMPROVE consolidation, memory injection into system prompt

### Modified Capabilities
<!-- No existing specs need requirement-level changes — these are new features -->

## Impact

- **`controllers/stream.py`** — Major UI rewrite (responsive CSS, theme toggle, thread sidebar, search). New endpoints: `/ai/threads`, `/ai/thread/search`. Modified: `/ai/stream` accepts `session_id` and persists history.
- **`models/ai_session.py`** — Extended with `thread_name`, `session_line_ids` (One2many to new model)
- **`models/ai_session_line.py`** — **NEW** model for individual messages (role, content, timestamp, tool_calls)
- **`models/ai_memory.py`** — Extended with quest memory extraction and system prompt injection
- **`models/ai_quest.py`** — `_quest_is_accessible` already exists; used for thread access control
- **`core/improve.py`** — Used by daily IMPROVE cron for memory consolidation (already exists, no changes)
- **`views/`** — New views for session line model (if needed in Odoo backend)
- **`security/ir.model.access.csv`** — Access for `ai.quest.session.line`

## Non-goals

- NOT implementing real-time collaboration (multiple users in same thread)
- NOT adding file/image upload to chat
- NOT building a dedicated mobile app — responsive web only
- NOT migrating pgVector semantic search (ilike first, pgVector later)
- NOT changing the AgentLoop or provider layer — this is UI + persistence
