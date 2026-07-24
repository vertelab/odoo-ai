# Tasks: Identity, Skills, Taskless

## Phase 1: Standalone ai_agent_core ✅

- [x] **T1.1** — Update `__manifest__.py` — remove ai_agent dependency, add own menu/security
- [x] **T1.2** — Create `security/ir.model.access.csv` with standalone access rights
- [x] **T1.3** — Create menu + actions (AI Orchestration)

## Phase 2: Identity ✅

- [x] **T2.1** — `models/ai_identity.py` — ai.identity model (soul, user_model, skills) — 133 rader
- [x] **T2.2** — `views/ai_identity_views.xml` — form/list/kanban views
- [x] **T2.3** — System prompt compilation from identity components (`_compute_system_prompt()`)

## Phase 3: Skills ✅

- [x] **T3.1** — `models/ai_skill.py` — ai.skill model (name, triggers, recipes, verify_cases) — 157 rader
- [x] **T3.2** — `views/ai_skill_views.xml` — skill views
- [x] **T3.3** — Skill assignment to agents/quests (`ai.quest.skill`, `ai.quest_skill.py`)

## Phase 4: Taskless ✅

- [x] **T4.1** — `core/detect.py` — scan before acting — 337 rader
- [x] **T4.2** — `core/route.py` — intelligent path selection — 312 rader
- [x] **T4.3** — `core/improve.py` — structured feedback loop — 288 rader
- [x] **T4.4** — `core/verify.py` — three-layer validation — 411 rader

## Phase 5: Tools & Graph ✅

- [x] **T5.1** — `core/tools.py` — OdooModelTools auto-generation, MCPTools, CustomTools — 596 rader
- [x] **T5.2** — pgGraph integration + graph_query tool (`core/tools.py`)
- [x] **T5.3** — Model catalog sync from Bifrost (`ai_provider.py` + weekly cron)

## Phase 6: Integrations ✅

- [x] **T6.1** — `core/budget.py` — budget enforcement (PAPER-004) — 230 rader
- [x] **T6.2** — `core/interrupt.py` — InterruptHandler (Discuss/WebUI/Auto) (HITL-001..006)
- [x] **T6.3** — `core/eval.py` — agent evaluator (PAPER-006)
- [x] **T6.4** — `core/context.py` — context management for AgentLoop
- [x] **T6.5** — Activity log via `ai.quest.session.line` (PAPER-007)

---

## Återstående arbete

### Hål 1: Kaizen loop (KAIZEN-001)
- [ ] **T7.1** — `core/kaizen.py` — weekly self-review agent
  - Agent wakes on schedule (cron)
  - Analyzes own performance: error rates, costs, patterns
  - Proposes improvements with evidence
  - Tracks metrics week-over-week
- **Tid:** ~3 dagar

### Hål 2: ONBOARD (TASK-005)
- [ ] **T8.1** — `core/onboard.py` — mine codebase for quest candidates
  - Codebase TODOs → latent automation opportunities
  - Data quality problems → monitoring quests
  - Recurring support tickets → automation candidates
  - Agent memory files (AGENTS.md) → explicit rules
- **Tid:** ~3 dagar

### Hål 3: Recipe library
- [ ] **T9.1** — Skapa `/srv/salt/ai/recipes/` med katalogstruktur
- [ ] **T9.2** — Migrera procedurer från thin router skills till recipes
- [ ] **T9.3** — Skill catalog sync (SHARE-007): auto-skapa `ai.skill` från recipe frontmatter
- **Tid:** ~2 dagar

### Hål 4: Identity learning (ID-006)
- [ ] **T10.1** — `/learn`-kommando i web-chat
  - Uppdaterar identity.user_model från interaktioner
  - Agent kan proaktivt föreslå identity-uppdateringar
  - Alla ändringar bekräftas av användaren
- **Tid:** ~1 dag

### Hål 5: Dynamic tool discovery (TOOL-007)
- [ ] **T11.1** — Mid-session tool registration
  - MCP servers kan registrera nya tools under session
  - Tool list refresh utan omstart
  - Agent frågar "vilka tools finns?"
- **Tid:** ~2 dagar

### Hål 6: Web UI-polish
- [ ] **T12.1** — Agent configuration panel (edit skills, tools, model, budget per agent from chat)
- [ ] **T12.2** — State inspector — debug view showing raw agent state
- [ ] **T12.3** — Budget overview — monthly spend vs limit with progress bar
- **Tid:** ~3 dagar

---

## Sammanfattning

| Status | Tasks | Estimerad tid |
|--------|-------|---------------|
| ✅ Klart | 22 tasks (T1.1–T6.5) | — (redan gjort) |
| ❌ Återstår | 10 tasks (T7.1–T12.3) | ~14 dagar |

**Notera:** Tasks.md var inaktuell — alla 13 ursprungliga tasks var redan genomförda men inte avprickade. 1638 rader kod över identity, skills, Taskless core, tools, graph, budget, interrupt, eval, och context.
