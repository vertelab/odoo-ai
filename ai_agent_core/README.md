# AI Agent Core (`ai_agent_core`)

Standalone AI agent engine for Odoo. Buzz-inspired architecture.
**Zero LangChain dependency.** Pure Python while-loop + httpx.

## Architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│                        AI_AGENT_CORE                                 │
│                                                                      │
│  ┌─────────────────────┐  ┌──────────────────────────────────────┐   │
│  │   AgentLoop          │  │   SupervisorLoop                     │   │
│  │   ─────────          │  │   ──────────────                     │   │
│  │   while True:        │  │   route(prompt) → specialist agent   │   │
│  │     response = llm   │  │   agent.run(prompt)                  │   │
│  │     if text → return │  │   fan-out → merge                    │   │
│  │     if tools → exec  │  │   keyword fallback                   │   │
│  │     if ctx_full → sum│  └──────────────────────────────────────┘   │
│  └──────────┬───────────┘                                            │
│             │                                                        │
│  ┌──────────▼──────────────────────────────────────────────────┐     │
│  │                    Provider Layer                             │     │
│  │  ┌─────────────────┐  ┌──────────────────────────────────┐   │     │
│  │  │ BifrostProvider  │  │ DirectProvider                    │   │     │
│  │  │ (gateway via     │  │ (native OpenAI, Anthropic,        │   │     │
│  │  │  OpenAI API)     │  │  DeepSeek, Google, Cerebras, Groq)│   │     │
│  │  └─────────────────┘  └──────────────────────────────────┘   │     │
│  │  BudgetEnforcingProvider (PAPER-004)                         │     │
│  └─────────────────────────────────────────────────────────────┘     │
│                                                                      │
│  ┌──────────────────────────────────────────────────────────────┐    │
│  │                    Tool System                                │    │
│  │  Tool dataclass → ToolRegistry → serialization (OpenAI + Anth)│    │
│  │  OdooModelTools (search_read, read, write, create, unlink)   │    │
│  │  MCPTools (auto-discover from MCP servers)                    │    │
│  │  CustomTools (user-defined via ai.tool Odoo model)            │    │
│  └──────────────────────────────────────────────────────────────┘    │
│                                                                      │
│  ┌──────────────────────────────────────────────────────────────┐    │
│  │                 Human-in-the-Loop                             │    │
│  │  InterruptHandler ABC → Discuss | WebUI | Auto handlers       │    │
│  │  Risk levels (safe → destructive) → approval thresholds       │    │
│  │  Mid-turn steering (drain_steer) + Clarification (HITL-007)   │    │
│  └──────────────────────────────────────────────────────────────┘    │
│                                                                      │
│  ┌──────────────────────────────────────────────────────────────┐    │
│  │                 Taskless Layer (TASK-001..004)                │    │
│  │  detect.py — scan environment before acting                   │    │
│  │  route.py  — intelligent path: existing → local → remote      │    │
│  │  improve.py — structured feedback: guidance → iterate (max 3) │    │
│  │  verify.py  — 3-layer validation: schema, reqs, tests         │    │
│  │  eval.py    — per-agent statistics + trend analysis           │    │
│  │  budget.py  — hard budget stops (PAPER-004)                   │    │
│  └──────────────────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────────────────┘
```

## Module Structure

```
ai_agent_core/
├── core/                   # Pure Python (no Odoo dependency)
│   ├── provider.py         # AIProvider ABC + BifrostProvider + DirectProvider (703 lines)
│   ├── loop.py             # AgentLoop: while-loop, parallel tools, cancel, clarify (565 lines)
│   ├── supervisor.py       # SupervisorLoop: router LLM + keyword fallback + fan-out (330 lines)
│   ├── tools.py            # Tool + ToolRegistry + OdooModelTools + MCP (596 lines)
│   ├── interrupt.py        # InterruptHandler ABC + Discuss/WebUI/Auto handlers (305 lines)
│   ├── context.py          # Auto-summarize + token estimation (74 lines)
│   ├── detect.py           # TASK-001: Environment scanning
│   ├── route.py            # TASK-002: Intelligent path selection
│   ├── improve.py          # TASK-003: Structured feedback loop
│   ├── verify.py           # TASK-004: Three-layer output validation
│   ├── budget.py           # PAPER-004: Budget enforcement
│   └── eval.py             # PAPER-006: Per-agent evaluations
├── models/                 # Odoo models (require Odoo runtime)
│   ├── ai_provider.py      # ai.provider — LLM provider configuration
│   ├── ai_model.py         # ai.model — individual models with capabilities
│   ├── ai_quest.py         # ai.quest — standalone quests + AICoworkerRun wizard
│   ├── ai_agent.py         # ai.agent — agents with identity, skills, tools, budget
│   ├── ai_session.py       # ai.quest.session — extended with token tracking
│   ├── ai_identity.py      # ai.identity — SOUL.md (personality, style, values)
│   ├── ai_skill.py         # ai.skill — reusable competencies (agentskills.io)
│   ├── ai_tool.py          # ai.tool — user-defined custom tools
│   └── ai_memory.py        # ai.memory — FAISS/pgvector memory
├── controllers/
│   └── stream.py           # SSE streaming + /ai/chat web UI
├── views/                  # Odoo XML views
├── security/               # ACL
└── tests/
    └── test_core.py        # 31 tests (644 lines)
```

## Quick Start

### 1. Python-only (no Odoo)

```python
from ai_agent_core.core.provider import BifrostProvider
from ai_agent_core.core.tools import ToolRegistry, builtin_tools
from ai_agent_core.core.loop import AgentLoop, AgentConfig

provider = BifrostProvider(virtual_key="opencode")
tools = ToolRegistry()
tools.register_many(builtin_tools())

loop = AgentLoop(
    provider=provider,
    tools=tools,
    config=AgentConfig(
        model="cerebras/gpt-oss-120b",
        max_rounds=10,
    ),
)

import asyncio
result = asyncio.run(loop.run("What is 2+2?"))
print(result.text)
```

### 2. With Supervisor (multi-agent)

```python
from ai_agent_core.core.supervisor import SupervisorLoop, SpecialistAgent

analyst = SpecialistAgent(
    name="analyst",
    description="Data analysis and reporting",
    loop=analyst_loop,
    triggers=["analyze", "report"],
)

supervisor = SupervisorLoop(
    router_provider=router_provider,
    agents=[analyst, support_agent],
)

result = asyncio.run(supervisor.run("Analyze Q2 sales"))
```

### 3. With Budget Enforcement

```python
from ai_agent_core.core.budget import BudgetTracker, BudgetEnforcingProvider

budget = BudgetTracker(limit=50.0)  # $50/month
safe_provider = BudgetEnforcingProvider(provider, budget)

loop = AgentLoop(provider=safe_provider, tools=tools, config=config)
# Will raise BudgetExhaustedError if limit exceeded
```

### 4. With Taskless (detect → route → improve → verify)

```python
from ai_agent_core.core.detect import EnvironmentDetector
from ai_agent_core.core.route import IntelligentRouter
from ai_agent_core.core.verify import OutputVerifier

# Detect: scan environment
detector = EnvironmentDetector()
env = detector.scan()  # Quick scan (no Odoo needed)

# Route: find best path
router = IntelligentRouter()
decision = router.route("List all customers", env_info=env)
print(decision.destination)  # "local" — can use search_read directly

# Verify: validate output
verifier = OutputVerifier()
result = verifier.verify(
    output="Customer: ACME Corp",
    requirements=["must contain Customer"],
    tests=[{"expected_contains": ["Customer"]}],
)
print(result.passed)  # True
```

### 5. With Evals

```python
from ai_agent_core.core.eval import EvalCase, AgentEvaluator

evaluator = AgentEvaluator()
runner = evaluator.create_runner(loop, model="gpt-4o", agent_name="assistant")

cases = [
    EvalCase(input="What is 2+2?", expected_contains=["4"], category="math"),
    EvalCase(input="Capital of Sweden?", expected_contains=["Stockholm"], category="geo"),
]

run = asyncio.run(runner.run_eval(cases))
print(f"Accuracy: {run.accuracy:.1%}")
print(f"Cost: ${run.total_cost:.4f}")
```

## Init Types

Each `ai.coworker` can have multiple active init types that determine HOW
the coworker is triggered. All 10 init types are auto-seeded on creation.

| Init Type | Trigger | Handler | Key Config |
|-----------|---------|---------|------------|
| `web_ui` | `/ai/chat` web UI | SSE streaming | `show_in_chat` |
| `chat` | Discuss private chat | `chat()` | `response_mode`, `chat_user_id` |
| `channel` | Discuss channel | `chat()` | `response_mode`, `channel_ids` |
| `mail` | Incoming email | `message_new` (mail-trigger) | `alias_name`, `mail_action`, `mail_reply_delay`, `mail_find_partner` |
| `cron` | Scheduled action | `cron()` | `cron_interval_number/type` |
| `server_action` | Button in form/list | `server_action()` | `server_action_use_wizard` |
| `powerbox` | `/` in text fields | `powerbox()` | `model_ids` binding |
| `webhook` | `POST /ai/webhook/<id>` | Webhook controller | `webhook_secret` |
| `openai_api` | `POST /ai/openai/<id>/v1/...` | OpenAI API controller | API key, rate limits |
| `manual` | Programmatic `run()` | `run()` | None |

### Response Modes (chat/channel)

- `always` — Respond to every message
- `mention` — Only respond when @mentioned (**default**)
- `trigger` — Only on matching trigger words

### Channel Reply Modes

- `public` — Reply in channel (**default**)
- `private` — Reply as direct message
- `thread` — Reply as thread

### Provider Factory

Providers are resolved via the chain:
`ai.coworker → ai.agent → ai.model → ai.provider`

`ProviderFactory` in `core/provider.py`:
- `from_coworker(coworker)` — from first agent
- `from_agent_rel(agent_rel)` — from specific agent
- `from_supervisor_agents(coworker)` — all agents
- `get_default_provider()` — via `ai_agent_core.default_model_id`

Supports: `bifrost`, `openai`, `anthropic`, `openrouter`, `deepseek`, `custom`

### Memory Architecture

- **Agent-level**: Permanent RAG on `ai.agent.rag_memory_ids` (handbooks, websites)
- **Session-level**: Ad-hoc uploaded docs in chat thread, injected into system prompt
- Both tiers support FAISS vector search

## Configuration

```python
@dataclass
class AgentConfig:
    model: str = "gpt-4o"               # Default model
    system_prompt: str = ""             # System prompt
    temperature: float = 0.7
    max_tokens: int = 4096
    max_rounds: int = 20                # Max loop iterations
    max_context_tokens: int = 128_000   # Trigger summarization
    tool_timeout: float = 30.0          # Seconds per tool
    llm_timeout: float = 120.0          # Seconds per LLM call
    max_tool_result_chars: int = 8000   # Truncation limit
    max_parallel_tools: int = 5         # Concurrent tool limit
    approval_threshold: int = 2         # Risk level for human approval
    max_clarifications: int = 3         # Max proactive questions
```

## Requirements Coverage

| System | Coverage | Status |
|--------|----------|--------|
| **Provider** (PROV-001..006) | 100% | ✅ Complete |
| **Agent Loop** (LOOP-001..007) | 100% | ✅ Complete |
| **Tool System** (TOOL-001..008) | 75% | ✅ Core + Odoo + MCP + Custom |
| **HITL** (HITL-001..010) | 70% | ✅ Handlers + Clarify + Approve |
| **Identity** (ID-001..008) | 60% | ✅ SOUL + Compilation + Templates |
| **Skills** (SHARE-001..008) | 50% | ✅ Model + Triggers + Recipes |
| **Taskless** (TASK-001..008) | 50% | ✅ DETECT + ROUTE + IMPROVE + VERIFY |
| **Budget + Evals** (PAPER-004,006) | 100% | ✅ Both implemented |
| **Supervisor** | 100% | ✅ Routing + Fan-out + Merge |
| **Sessions** (SESS-001..004) | 100% | ✅ Model + Lifecycle + Persistence |

## Workspace (Odoo Mind Workspace)

Workspace-lagret implementerar Second Brain-metodiken (CODE/PARA) som ett
metodiklager ovanpå OKF-substratet — producerar resultat i Odoo Core.

### Modeller

- `workspace.para.container` + `workspace.para.ref` — PARA-behållare per
  användare (project/area/resource/archive) med polymorfa referenser
  (aldrig kopior). ADD-only-koncept placeras via refs, inte writes.
- `workspace.activity.suggestion` — aktivitetsförslag i agendan (HITL-kort
  med Godkänn/Avvisa/Omplanera/Varför, diff före/efter, mötets ankare).
- `workspace.gap.engine` — GAP-analys (target−current från KR,
  deadline−idag från SMART) + `build_agenda()` (query-byggare).
- `executive.summary.interface` — distill-lager L2 (executive summary) /
  L3 (synopsis), ADD-only versionering, generated_by (cron/nightly/
  project_close).
- `ai.okf.concept` — utökad med inbox (in_inbox), `create_from_mail()`
  (res.partner find/create + eml-bilaga), `action_place_in_para()`,
  `action_nudge_para()`, `render_attribution_html()` (klickbara källor +
  osäker-flagga), `action_publish_to_company()/action_publish_to_channel()`.
- `ai.coworker` — utökad med `injection_level` (L0-L3-styrning),
  autonomi-panel (budget_kr_monthly, max_actions_per_day,
  hitl_threshold) och `example_prompts` (katalog).

### Vyer (menyn "Odoo Mind")

Agenda · Inbox · Approvals · PARA · Search · Coworkers · Coworker-katalog

### Flöden

- **Capture**: mail → OKF-koncept (partner + eml-bilaga), inbox-vy.
- **Organize**: inbox→PARA (manuell + auto för knowledge/archive + nudge).
- **Distill**: L0→L1→L2→L3 löpande + vid projektavslut (lessons learned).
- **Express**: förslag → Odoo-objekt via OpenWorker-gate (HITL) —
  permission engine i `core/permission.py`.
- **Publicera**: personligt→company explicit (ny company-kopia, attribution
  behålls), → Discuss-kanal via message_post.

### Referenser

- Tiago Forte, *Building a Second Brain* (CODE + PARA)
- Niklas Luhmann, *Zettelkasten* (koncept/attribution)
- OpenWorker-mönster: PLAN-openworker-lessons.md (permission modes +
  plan-before-action)

## Running Tests

```bash
cd /usr/share/odoo-ai
python3 -m unittest ai_agent_core.tests.test_core -v
python3 -m unittest ai_agent_core.tests.test_openworker_gate -v
```

Odoo-integrationstester (kräver DB): `checkmodule -d <db> -m ai_agent_core -t`
(inkl. `tests/test_workspace.py`).

## Dependencies

- `httpx` — async HTTP client
- `tenacity` — retry logic
- Odoo 18+ (for models and controllers)
- Bifrost LLM Gateway (for `BifrostProvider`)
- Salt pillar (for API keys in `DirectProvider`)

## License

AGPL-3 — Vertel AB

---

## Skill-mall (affärsprocessguider) — odoo-model-tools

Affärsprocess-skills (`ai.skill`) ska följa denna mall så att agenter vet
*när*, *varför* och *hur* de interagerar med Odoo-modeller:

```
# Skill: <domän> — <beskrivning>
## Scope            → moduler + modeller; INSTALL-CHECK:
                     "uteslut sektioner vars modul inte är installerad
                     (ir.module.module)"
## <App>-sektion    → per app: modeller, affärsprocesser (steg + HITL),
                     metodtabell (Metod/När/HITL)
## Verktygshintar   → describe_model / odoo_search / okf_search / graph_query
## Trigger-nyckelord → crm, offert, faktura, lager, …
```

Kodverifierade exempel: `data/skill_odoo_core.xml` (CRM-sektionen bygger på
faktisk Odoo 18-kod: crm.lead state-maskin, convert_opportunity,
action_set_won/lost m.fl.). Regler: skriv aldrig `state` direkt — anropa
affärsmetoder (action_*/button_*) via `odoo_call_method`; HITL-policy per
operation anges i skillen (affärs-HITL) + permission engine som backstop.

## Bridge-repo-mönster för domänspecifika skills

Domänspecifika skills (`<domän>_ai`) skapas i respektive bridge-repo som
data-XML, enligt bridge-standarden:

```
odoo-<domän>/<domän>_ai/data/skills.xml
  <record id="skill_<domän>_<x>" model="ai.skill">
    <field name="name">…</field>
    <field name="category">…</field>
    <field name="recipe_text"><![CDATA[…]]></field>
  </record>
```

- AI-förmågor (coworkers, skills) ligger ENBART i `_ai`-moduler — domän-core
  är ren.
- Skills kopplas till agenter via `ai.agent.skill_ids` (data-XML `ref`).
- Install-check-instruktionen gör att sektioner för oinstallerade moduler
  utesluts vid körning.

## AI-tool-beskrivningsmall (ai-tool-access-capabilities)

`ai.tool.description` är det kontrakt LLM:en läser vid verktygsval. Skriv den
som en strukturerad mall (AI-beskrivning), inte en enradare:

```
syfte:    vad verktyget uppnår (inte bara vad det gör)
när:      när det ska användas (symptom, villkor)
när inte: när det INTE ska användas — peka på rätt verktyg
          ("föredra state.show_sls före state.apply")
exempel:  realistiskt anrop med parametrar
output:   förväntad resultatform
guardrail: om verktyget kräver godkännande — nämn det (informativt)
```

Guardrails är ALDRIG advisory: enforcement sker strukturellt via
`risk_level` (destructive/execute → alltid HITL) + PermissionEngine. En
skill (`ai.skill.recipe_text`) får beskriva arbetsmönster och HITL-policy,
men kan aldrig upphäva eller ersätta motorns grindar.

## Access-grupper och förmågeserialisering (ai-tool-access-capabilities)

**Access (`ai.tool.group_ids`, M2M `res.groups`):** vem som får använda
verktyget. Tom = obegränsat (HITL via risk_level gäller ändå). Två lager:
1) filtrering vid registrering (`ai.coworker.run()`, stream-chatten) —
LLM:en ser aldrig otillåtna verktyg; 2) PermissionEngine nekar gruppbundna
verktyg utan korsning (defense-in-depth). Icke-interaktiv (cron/webhook/mail)
= coworkerns egna `group_ids` som access-grund.

**Förmågor (`ai.tool.capability`):** serialiseringsenhet — namn +
AI-beskrivning + medlemmar. Separerad från access: `group_ids` styr *vem*,
förmågan styr *vad LLM:en ser*. Läge per coworker (`serialize_capabilities`):
- `flat` (default) — individuella verktyg
- `enum` — en Tool per förmåga med operation-enum (max 8 operationer; fler
  delas). Minimal kontext, bra för små modeller.
- `namespace` — individuella verktyg behålls (parallellitet) + förmågans
  beskrivning i systemprompten.

Access-filtrering sker ALLTID före serialisering — otillåten medlem saknas
både som verktyg och som enum-operation.

## Deploy (odoo-model-tools)

- Ändringar i ai_agent_core kräver **versionbump i __manifest__.py** + `sudo checkmodule -d <db> -m ai_agent_core` (checkmodule kör `--init` — migrations körs inte, så adoption av legacy-poster sker via `<function>` i data-XML).
- Rene Python-ändringar (core/*.py, controllers): `sudo systemctl restart odoo` räcker.
- Tester: `python3 -m unittest ai_agent_core.tests.test_core` (core) · `checkmodule -d <db> -m ai_agent_core -t` (Odoo-integration).
- Känd begränsning: demo_data.xml (Order-Vakten) har ett befintligt ParseError — påverkar inte produktion.

## Mail-triggers (incoming mail actions)

`mail`-initieringen kan göra mer än att bara svara. Via `mail_action` väljer man
vad som sker när ett mail anländer till aliaset (`alias@företagets-mail-domän`):

| mail_action | Beskrivning |
|-------------|-------------|
| `reply` | Köra medarbetaren på mailinnehållet och posta svaret på sessionstråden. |
| `create_record` | Skapa/uppdatera ett record i `mail_target_model_id` från mailinnehållet. |
| `invoice_ai` | Leverantörsfaktura-flöde: hitta/skapa `res.partner` från avsändaren → OCR-läs fakturan (pypdf) → skapa `account.move` (in_invoice) via Fakturaanalys-agenten. |

Övriga inställningar:
- `mail_reply_delay` (min): fördröjt svar — postas av cronen
  *AI: Posta fördröjda mail-svar* (körs varje minut, `_post_pending_reply`).
- `mail_find_partner` (invoice_ai): sök/skapa `res.partner` från avsändaren.
- `mail_invoice_agent_ids` (invoice_ai): agenterna `Mail → Partner` och
  `Fakturaanalys` (skapa fler via AI Medarbetare → Initiering → Mail).

Flödet körs med `_ai_auto_approve`-kontext (AUTO-mode) — mailbearbetning är en
tillitsfull automatisk kontext; `odoo_create`/`odoo_write`/`odoo_call_method`
fastnar inte i HITL-kön. Datamedarbetaren *Faktura-Assistenten* är ett exempel
(mail_action=invoice_ai, alias `faktura`).

> Kommande: bryt ut faktura-flödet till en egen modul (`account_invoice_ai`-
> integration) när det är dags — logiken ligger idag i
> `models/ai_session.py` (`_process_invoice_mail`, `_resolve_mail_partner`).
