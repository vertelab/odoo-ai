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
│   ├── ai_quest.py         # ai.quest — standalone quests + AIQuestRun wizard
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

## Running Tests

```bash
cd /usr/share/odoo-ai
python3 -m unittest ai_agent_core.tests.test_core -v
```

## Dependencies

- `httpx` — async HTTP client
- `tenacity` — retry logic
- Odoo 18+ (for models and controllers)
- Bifrost LLM Gateway (for `BifrostProvider`)
- Salt pillar (for API keys in `DirectProvider`)

## License

AGPL-3 — Vertel AB
