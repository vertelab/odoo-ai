# ai_agent_core_academic — Academic Paper Writing Module

## Overview

Adds an 8-agent academic paper writing pipeline to ai_agent_core.

## What it provides

- **1 skill**: "Academic Paper Writing" (full 7-phase pipeline recipe)
- **8 agents**: Intake Agent, Literature Strategist, Structure Architect, Argument Builder, Draft Writer, Citation Compliance, Peer Reviewer, Formatter
- **1 quest**: "Academic Paper Writer" (supervisor mode, web_ui init_type)

## Pipeline Phases

1. Intake — configuration interview
2. Literature Search — search strategy + annotated bibliography
3. Structure Design — IMRaD/Thematic outline
4. Argument Building — claim-evidence chains (CER)
5. Draft Writing — full-text draft
6. Citation Compliance — verify against sources
7. Formatting — output conversion (LaTeX, DOCX, PDF, Markdown)

## Agent Models

All agents use `cerebras/gpt-oss-120b` via Bifrost.
