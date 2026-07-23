# Tasks: Identity, Skills, Taskless (Change 3 — Phase 1)

## Phase 1: Standalone ai_agent_core

- [ ] **T1.1** — Update `__manifest__.py` — remove ai_agent dependency, add own menu/security
- [ ] **T1.2** — Create `security/ir.model.access.csv` with standalone access rights
- [ ] **T1.3** — Create menu + actions (AI Orchestration)

## Phase 2: Identity (SOUL.md)

- [ ] **T2.1** — `models/ai_identity.py` — ai.identity model (soul, user_model, skills)
- [ ] **T2.2** — `views/ai_identity_views.xml` — form/list/kanban views
- [ ] **T2.3** — System prompt compilation from identity components

## Phase 3: Skills

- [ ] **T3.1** — `models/ai_skill.py` — ai.skill model (name, triggers, recipes, verify_cases)
- [ ] **T3.2** — `views/ai_skill_views.xml` — skill views
- [ ] **T3.3** — Skill assignment to agents/quests

## Phase 4: Taskless (detect/route/improve/verify)

- [ ] **T4.1** — `core/detect.py` — scan before acting
- [ ] **T4.2** — `core/route.py` — intelligent path selection
- [ ] **T4.3** — `core/improve.py` — structured feedback loop
- [ ] **T4.4** — `core/verify.py` — three-layer validation
