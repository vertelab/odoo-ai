# Spec: Shared Skills & Agent Administration

## Requirements

### SHARE-001 [NOW] — Unified recipe library
The system MUST provide a shared recipe library at `/srv/salt/ai/recipes/`.
- Recipes are the canonical source of domain knowledge
- All agent types (ai_quest, Pi-agent, opencode) read the same recipes
- Recipe format: Markdown with YAML frontmatter (name, version, topics)
- Recipes are versioned in git, distributed via Salt
- A change to a recipe propagates to all agents automatically
- Recipe topics map user intent to specific recipe files

### SHARE-002 [NOW] — Thin router skills
Agent-specific skill files MUST be thin routers, not knowledge containers.
- Skill body: trigger description + topic-to-recipe mapping table
- No embedded procedures in the skill file
- Agent-specific tool instructions separated from shared knowledge
- Skill files reference recipes by path: `cat /srv/salt/ai/recipes/<category>/<topic>.md`
- ai_quest skills: ai.skill records in Odoo (editable via admin UI)
- Pi-agent skills: SKILL.md files in ~/.pi/agent/skills/ (distributed via Salt)

### SHARE-003 [NEXT] — Recipe migration from existing skills
The system SHOULD migrate existing duplicated skills to the shared library.
- Extract knowledge from ~47 Pi-skills and ~27 Opencode-skills
- Identify duplicates (~25 skills exist in both)
- Create unified recipes in /srv/salt/ai/recipes/
- Replace old skill files with thin routers referencing recipes
- Remove old duplicated copies after migration verified

### SHARE-004 [NOW] — Agent administration in Odoo
The system MUST provide unified agent administration in the Odoo interface.
- `ai.agent` model extended to represent ALL agent types:
  - ai_quest agents (internal Odoo agents)
  - Python Pi-agents (external Python processes)
  - External agents (opencode, custom)
- Agent type determines available configuration fields
- All agents visible in the same kanban/list/form views
- Agent status (online/offline/error) tracked via heartbeat

### SHARE-005 [NOW] — Python Pi-agent administration
The system MUST support Python Pi-agent lifecycle management from Odoo.
- Register a Pi-agent: name, host, API key, skills, tools
- Start/stop/restart Pi-agent processes via Salt (salt '<minion>' state.apply ai.pi_agent)
- Monitor agent status via heartbeat (discuss.channel presence)
- View agent activity log (ai.quest.session.line records)
- Configure agent skills via the same skill picker as ai_quest agents
- Pi-agent skills are the same ai.skill records — no duplication

### SHARE-006 [NOW] — Skill assignment UI
The system MUST provide a unified skill assignment interface.
- Skill picker widget: search, filter by category, drag-and-drop
- Skills assigned to agents (ai.agent.skill_ids)
- Skills can be overridden per quest (ai.quest.skill_ids)
- Available skills list sourced from ai.skill catalog
- Both ai_quest and Pi-agents use the same skill catalog

### SHARE-007 [NEXT] — Skill catalog sync
The system SHOULD keep the skill catalog in sync with the recipe library.
- `ai.skill` records auto-created from /srv/salt/ai/recipes/ frontmatter
- Recipe content stored as ai.skill.recipe_text or linked file
- Skills can be enabled/disabled per organization
- Skills can be versioned (track recipe changes)

### SHARE-008 [NOW] — Agent type model
The `ai.agent` model MUST distinguish between agent runtimes:

```python
class AIAgent(models.Model):
    _inherit = 'ai.agent'
    
    agent_type = fields.Selection([
        ('odoo', 'Odoo Agent (internal)'),
        ('pi_python', 'Pi Agent (Python)'),
        ('pi_node', 'Pi Agent (Node.js)'),
        ('opencode', 'Opencode Agent'),
        ('external', 'External Agent (API)'),
    ], required=True, default='odoo')
    
    # Odoo-agent fields (existing)
    ai_agent_llm_id = fields.Many2one('ai.agent.llm')
    ai_tool_ids = fields.Many2many('ai.tool')
    
    # Pi-agent fields (new)
    host_minion = fields.Char()           # Salt minion where agent runs
    api_key_id = fields.Many2one('ai.api_key')
    process_status = fields.Selection([
        ('stopped', 'Stopped'),
        ('running', 'Running'),
        ('error', 'Error'),
    ])
    last_heartbeat = fields.Datetime()
    log_tail = fields.Text()
    
    # Shared fields (all types)
    skill_ids = fields.Many2many('ai.skill')
    provider_id = fields.Many2one('ai.provider')
    model_id = fields.Many2one('ai.model')
    budget_limit = fields.Float()
    budget_used = fields.Float()
```

## Agent Administration UI

```
┌──────────────────────────────────────────────────────────────────────────┐
│         AI AGENT ADMINISTRATION — SAMMA GRÄNSSNITT                       │
│                                                                          │
│  AI → Agents (kanban-vy)                                                 │
│  ────────────────────────                                                │
│                                                                          │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐                   │
│  │ Kundanalys   │  │ Bokföring    │  │ Infra Pi     │                   │
│  │ ─────────── │  │ ─────────── │  │ ─────────── │                   │
│  │ 🟢 Odoo      │  │ 🟢 Odoo      │  │ 🟢 Pi-Python │                   │
│  │ internal     │  │ internal     │  │ hermes       │                   │
│  │ 3 skills     │  │ 8 skills     │  │ 5 skills     │                   │
│  │ GPT-4o       │  │ Claude 4     │  │ DeepSeek V4  │                   │
│  │ [▶ Start]    │  │ [⏹ Stop]    │  │ [🔄 Restart] │                   │
│  └──────────────┘  └──────────────┘  └──────────────┘                   │
│                                                                          │
│  Agent-formulär (gemensamt för alla typer):                               │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │  Name: [Infra Pi                    ]  Type: [Pi-Python ▾]       │   │
│  │                                                                  │   │
│  │  ── Runtime ──                                                   │   │
│  │  Host: [hermes                     ]  Status: 🟢 Running         │   │
│  │  API Key: [●●●●●●●●               ]  Heartbeat: 2s ago           │   │
│  │                                                                  │   │
│  │  ── Model ──                                                     │   │
│  │  Provider: [Bifrost ▾]              Model: [claude-sonnet-4 ▾]   │   │
│  │                                                                  │   │
│  │  ── Skills ──                                                    │   │
│  │  ┌──────────────────────────────────────────────────────────┐   │   │
│  │  │ 🎯 Odoo 18 Development                          [x]       │   │   │
│  │  │ 📊 Swedish VAT Compliance                       [x]       │   │   │
│  │  │ 🖥️ SaltStack Operations                         [x]       │   │   │
│  │  │ 📸 Agent Browser (screenshots)                 [x]       │   │   │
│  │  │ 🔍 Graph Query (Cypher)                        [x]       │   │   │
│  │  │ [+ Add Skill...]                                         │   │   │
│  │  └──────────────────────────────────────────────────────────┘   │   │
│  │                                                                  │   │
│  │  ── Budget ──                                                    │   │
│  │  Monthly limit: [$500.00          ]  Used: $127.34 (25%)        │   │
│  │                                                                  │   │
│  │  ── Activity ──                                                  │   │
│  │  2026-07-23 14:32  ✅ Kundanalys — 342 tokens, $0.12            │   │
│  │  2026-07-23 14:15  ✅ Statusrapport — 815 tokens, $0.28         │   │
│  │  2026-07-23 13:00  ⚠️ Timeout — escalated to human             │   │
│  └──────────────────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────────────────┘
```

## Non-requirements
- NOT building a full SaltStack UI for agent process management (use existing Salt)
- NOT migrating all existing skills at once (incremental, start with most duplicated)
- NOT replacing Opencode agents (they remain as a separate agent type)
- NOT building agent-to-agent chat in the admin UI (use discuss.channel)
