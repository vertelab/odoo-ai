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
