# Design: AI Agent Core Loop & Provider Layer

## Architecture Overview

```
┌──────────────────────────────────────────────────────────────────────────┐
│                      AI_AGENT_CORE — SUBSYSTEMS                          │
│                                                                          │
│  ┌──────────────────────────────────────────────────────────────────┐    │
│  │                     agent-loop subsystem                          │    │
│  │                                                                  │    │
│  │  AgentLoop                        SupervisorLoop                 │    │
│  │  ─────────                        ──────────────                 │    │
│  │  while True:                      agents: dict[name, AgentLoop]  │    │
│  │    response = llm.chat(...)       route(prompt) → agent          │    │
│  │    if text → return               agent.run(prompt)              │    │
│  │    if tools → execute → continue  return result                  │    │
│  │    if ctx_full → summarize                                        │    │
│  └──────────────────────────────────────────────────────────────────┘    │
│                                                                          │
│  ┌──────────────────────────────────────────────────────────────────┐    │
│  │                    provider-layer subsystem                       │    │
│  │                                                                  │    │
│  │  AIProvider (ABC)                                                │    │
│  │  ├── chat(model, messages, tools, stream) → Response             │    │
│  │  ├── fetch_models() → list[AIModelInfo]                          │    │
│  │  └── chat_stream(model, messages, tools) → AsyncIterator[Token]  │    │
│  │                                                                  │    │
│  │  BifrostProvider                    DirectProvider                │    │
│  │  ──────────────                     ──────────────                │    │
│  │  base_url: bifrost:8080/v1          provider: openai|anthropic    │    │
│  │  virtual_key: opencode|dina         api_key: från pillar         │    │
│  │  OpenAI-format rakt igenom          Direkt httpx mot API          │    │
│  └──────────────────────────────────────────────────────────────────┘    │
│                                                                          │
│  ┌──────────────────────────────────────────────────────────────────┐    │
│  │                    agent-session subsystem                        │    │
│  │                                                                  │    │
│  │  AISession (ärver ai.quest.session)                              │    │
│  │  ├── history: list[Message]       ← konversations-historik       │    │
│  │  ├── tools: list[Tool]            ← MCP + Odoo-modeller         │    │
│  │  ├── token_usage: TokenUsage      ← input/output tokens          │    │
│  │  └── config: AgentConfig          ← max_rounds, timeout, etc.    │    │
│  └──────────────────────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────────────────────┘
```

## Decisions

### D1 — Agent loop is a while-loop, not a StateGraph

The core loop is a simple Python `while` loop. No graph compilation, no state machine.

```python
class AgentLoop:
    async def run(self, prompt: str, history: list[Message], tools: list[Tool]) -> AIMessage:
        messages = history + [HumanMessage(content=prompt)]
        
        for round_num in range(self.config.max_rounds or 999):
            response = await self.provider.chat(
                model=self.config.model,
                messages=messages,
                tools=self._tool_defs(tools),
            )
            
            if response.text and not response.tool_calls:
                return response.text  # done
            
            if response.tool_calls:
                results = await self._execute_tools(response.tool_calls, tools)
                messages.append(AssistantMessage(response.text, response.tool_calls))
                messages.extend(results)
                continue
            
            if self._context_too_large(messages):
                messages = await self._summarize(messages)
                continue
        
        raise MaxRoundsExceeded()
```

**Why:** Buzz proved this works in production at Block/Square. The entire agent is auditable in a single sitting. No LangGraph dependency. No LangChain dependency. The `_summarize` method (context handoff) is the only complex part and it's a single LLM call.

**Alternative rejected:** Keep LangGraph StateGraph. It adds 20+ dependencies for features we use 5% of (checkpointing, streaming, interrupts). The graph model is harder to debug than a simple loop.

### D2 — Provider is an ABC with two implementations

```python
class AIProvider(ABC):
    @abstractmethod
    async def chat(self, model, messages, tools, stream=False) -> ChatResponse: ...
    @abstractmethod
    async def fetch_models(self) -> list[ModelInfo]: ...

class BifrostProvider(AIProvider):
    """OpenAI-compatible via Bifrost gateway."""
    base_url = "http://192.168.11.150:8080/v1"
    virtual_key = "opencode"
    # Uses httpx directly — no OpenAI SDK needed

class DirectProvider(AIProvider):
    """Native provider SDKs for features Bifrost doesn't proxy."""
    provider: Literal["openai", "anthropic", "google"]
    # Uses provider-specific SDK or direct httpx
```

**Why:** Bifrost handles 80% of traffic (text, tools, streaming) with a single integration. DirectProvider covers vision, embeddings, TTS, and provider-specific features. The `model.provider_type` field on `ai.model` switches between them.

**Alternative rejected:** Multiple provider-specific classes (OpenAIProvider, AnthropicProvider, etc.). Bifrost makes these redundant for chat completions.

### D3 — Supervisor loop is a thin router, not a graph node

```python
class SupervisorLoop:
    def __init__(self, agents: dict[str, AgentLoop], router_llm: AIProvider):
        self.agents = agents
        self.router = router_llm
    
    async def run(self, prompt: str) -> AIMessage:
        # 1. Ask router LLM which agent should handle this
        choice = await self._route(prompt)
        # 2. Run the chosen agent's loop
        result = await self.agents[choice].run(prompt)
        # 3. Optionally: summarize with router if multiple agents needed
        return result
```

**Why:** Most multi-agent patterns are simple routing. The router LLM picks the specialist, the specialist does the work. Complex multi-step orchestration (agent A → agent B → agent C) is a future concern.

### D4 — Tool registry via MCP + Odoo models

Tools are registered by name. The loop doesn't care if a tool is an MCP server, an Odoo model search, or a shell command.

```python
@dataclass
class Tool:
    name: str
    description: str
    parameters: dict  # JSON Schema
    handler: Callable[..., Awaitable[str]]

class ToolRegistry:
    def register_odoo_model(self, model_name: str, methods: list[str]): ...
    def register_mcp_server(self, server_name: str, tools: list[ToolDef]): ...
    def get(self, name: str) -> Tool: ...
```

### D5 — Context handoff via summarize-then-continue

When estimated tokens exceed the model's context window, the agent summarizes its own history:

```python
async def _summarize(self, messages: list[Message]) -> list[Message]:
    summary_prompt = "Summarize the conversation so far, preserving key facts."
    summary = await self.provider.chat(
        model=self.config.model,
        messages=messages + [HumanMessage(summary_prompt)],
        tools=[],  # no tools during summarization
    )
    return [SystemMessage(f"Previous conversation summary:\n{summary.text}")]
```

**Why:** Buzz uses this pattern. It's one LLM call, no vector DB needed. The summarized context is injected as a system message.

## Risks & Trade-offs

- **[Egen loop = inget checkpoint-stöd]**: LangGraphs PostgresSaver ger persistence. Vi måste bygga egen session-serialisering. Mitigation: `ai.quest.session` har redan `session_line_ids` med full history.
- **[Bifrost är SPOF]**: Om Bifrost är nere → alla chat-anrop failar. Mitigation: DirectProvider som fallback, configurerad per model.
- **[Summarize kan förlora detaljer]**: En summarisering tappar alltid information. Mitigation: Buzz har kört detta i production i månader — det fungerar för de flesta use cases. För kritiska detaljer: öka context window eller använd modeller med större fönster.
- **[Ingen LangChain tool-calling]**: Måste implementera tool-calling-serialisering själv. Mitigation: OpenAI-formatet är väldokumenterat. Anthropic har sitt eget format. Båda är ~50 rader serialisering var.

## Data Model Changes

### New: ai.model (extends ai.agent.llm conceptually)

```
ai.model
├── name: Char                    "claude-sonnet-4"
├── provider_type: Selection      bifrost | direct
├── bifrost_model: Char           "openrouter/anthropic/claude-sonnet-4"
├── bifrost_virtual_key: Selection opencode | dina | plastshop
├── direct_provider: Selection    openai | anthropic | google | cerebras | groq
├── direct_model: Char            "claude-sonnet-4-20250514"
├── context_window: Integer       200000
├── max_output_tokens: Integer    16384
├── capabilities: Json            {"vision": true, "tools": true, ...}
├── cost_input_1k: Float          0.003
├── cost_output_1k: Float         0.015
└── status: Selection             active | deprecated | beta
```

### Extended: ai.quest.session

```
ai.quest.session (additional fields)
├── config_json: Text             AgentConfig serialized
├── history_json: Text            Message history serialized
├── token_input: Integer          Accumulated input tokens
├── token_output: Integer         Accumulated output tokens
└── cost_estimated: Float         Estimated cost
```
