# ai_agent_core_strategy — Strategy Skills Module

## Overview

Adds 21 AI skills, 5 agents, and 3 quests for business strategy to ai_agent_core.

## Skills (21)

| Category | Skills |
|----------|--------|
| Core strategy | Business Model Canvas, SWOT Analysis, Value Proposition Canvas, OKR Framework, Porter's Five Forces, Blue Ocean Strategy |
| Analysis | BCG Matrix, Ansoff Matrix, MECE Issue Tree, Hypothesis Tree, Root Cause Analysis, Risk Matrix, Value Chain Analysis |
| Financial | Financial Forecast, TAM/SAM/SOM, Unit Economics, Cost-Plus Pricing, Value-Based Pricing, Freemium Packaging |
| General | RACI Matrix, Odoo Strategy Context |

## Agents (5)

| Agent | Model | Skills |
|-------|-------|--------|
| Strategist | cerebras/gpt-oss-120b | BMC, VPC, SWOT, Porter, Blue Ocean |
| Analyst | cerebras/gpt-oss-120b | MECE, Hypothesis Tree, Root Cause, Risk Matrix, BCG, Ansoff |
| Financial Analyst | cerebras/gpt-oss-120b | Financial Forecast, TAM/SAM/SOM, Unit Economics, Pricing |
| Strategy Writer | cerebras/gpt-oss-120b | OKR, RACI, Value Chain, Odoo Context |
| Executor | anthropic/claude-sonnet-4 | ALL 21 skills (supervisor orchestrator) |

## Quests (3)

| Quest | Init Type | Agents | Description |
|-------|-----------|--------|-------------|
| Strategy Composer | powerbox | 5 (all) | Generate plans from strategy records |
| Strategy Advisor | chat | 2 (Strategist, Executor) | Free-form strategy consultant |
| Strategy Review | manual | 1 (Executor) | Weekly automated review |
