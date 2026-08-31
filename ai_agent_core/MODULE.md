# ai_agent_core — AI Agent Core Module

## Overview

Standalone AI agent engine for Odoo. No LangChain dependency. Provides:

- **Agent Loop**: Buzz-inspired while-loop with Bifrost provider
- **Web Chat**: `/ai/chat` with SSE streaming, threads, slash commands
- **Models**: ai.quest, ai.agent, ai.skill, ai.tool, ai.identity, ai.model, ai.provider
- **Skills system**: Reusable competencies with recipes
- **Identity system**: Agent personality (SOUL.md)

## Core Models

| Model | Description | Key Fields |
|-------|-------------|------------|
| ai.quest | AI Quest — the top-level unit | name, description, init_type, is_supervisor, identity_id |
| ai.agent | AI Agent — a specialized worker | name, bifrost_model, direct_model, provider_type, skill_ids |
| ai.quest.agent | M2M: quest ↔ agent assignment | quest_id, agent_id, sequence |
| ai.skill | Reusable competency | name, recipe_text, category, trigger_keywords |
| ai.identity | Agent personality/soul | name, system_prompt, style, scope, skill_ids |
| ai.model | AI model with capabilities | name, is_vision, has_streaming, context_window, sys_multiplier |
| ai.provider | AI provider (Bifrost, OpenAI, etc.) | name, provider_type, base_url |
| ai.tool | Reusable tool | name, description, risk_level |
| ai.memory | Quest memory (RAG facts) | name, content, category, importance |
| ai.tag | Lightweight AI tags | name, color |

## Init Types

Quests can be triggered in multiple ways via `ai.quest.init_type`:

| Type | Description | Auto-created |
|------|-------------|-------------|
| web_ui | Public web chat at /ai/chat | show_in_chat flag |
| chat | Private Discuss chat | Bot user |
| channel | Discuss team channel | Channel record |
| mail | Email ingestion | Mail alias |
| cron | Scheduled execution | ir.cron record |
| server_action | UI-triggered action | Server action |
| powerbox | Context-aware from records | model_ids binding |
| manual | Manual trigger only | — |
| openai_api | OpenAI-compatible endpoint | API key |

## Key Methods on ai.quest

- `run(prompt, system_prompt=None)` — synchronous execution via AgentLoop
- `get_available_skills()` — union of agent skills, identity skills, quest copies
- `powerbox(prompt, res_model, res_id)` — context-aware execution
- `action_run_scheduled()` — cron-triggered execution

## Agent Configuration

```python
agent.provider_type in ('bifrost', 'direct')
agent.bifrost_model  # e.g. 'cerebras/gpt-oss-120b'
agent.direct_model   # e.g. 'claude-sonnet-4-20250514'
agent.skill_ids      # M2M to ai.skill
```

## Session Accounting (session-audit, 18.0.1.163)

`ai.coworker.session` + `ai.coworker.session.line` är granskningsspåret för
alla AI-konversationer (web-chat `/ai/chat`, openai_api `/ai/v1`,
quest.run(), powerbox, mail). Bokföring per meddelande:

- **User-rad** bär requestens **input-tokens** (`token_input`, `token_output=0`)
  — prompt-kostnaden för det meddelandet.
- **Assistant-rad** bär **output-tokens** (`token_output`, `token_input=0`)
  samt `debug_info` (resonemang), `source_urls` och `tool_calls` (JSON-lista
  `[{name, preview}]`).
- **Tool-rader** (`role='tool'`) sparar varje verktygsanrop med `tool_name`,
  preview och `sys_token_cost` som `token_input` (multiplier 1.0).
- `token_sys` per rad = `(token_input + token_output) × sys_multiplier`;
  `session.token_sys` = Σ rader ≈ `(input+output) × multiplier` (oförändrad
  totalsumma jämfört med när input+output låg på samma rad).

Verklig token-usage i streaming:

- `TokenEvent` bär nu `input_tokens`/`output_tokens`; providern fyller dem
  via `stream_options.include_usage` (OpenAI-kompatibelt, med 400-retry utan
  för strikta gateways) eller Anthropic `message_start`/`message_delta`.
- `StreamingAgentLoop.run_stream` aggregerar usage över alla rundor och
  sätter totaler på den slutgiltiga `done`-händelsen.
- `/ai/stream` vidarebefordrar dem som `input_tokens`/`output_tokens` i
  done-SSE; frontend skickar dem till `/ai/threads/<id>/respond`
  (fallback: tidigare estimat).

Status-/livscykel (`/new`-semantik):

- `POST /ai/threads/<id>/close` — stänger tråden (`status='done'`,
  `finish_reason='closed'`). Anropas av web-UI:ets "+ Ny tråd".
- `POST /ai/threads` (thread_create) stänger användarens övriga aktiva
  sessioner (`finish_reason='new_session'`) — en ny konversation markerar
  den gamla som avslutad. Idempotent.
- `GET /ai/threads/<id>` returnerar nu per-meddelande-kontext: `status`,
  `finish_reason`, `debug_info`, `tool_calls`, `model_real`, `token_input`,
  `token_output`, `token_sys`.
