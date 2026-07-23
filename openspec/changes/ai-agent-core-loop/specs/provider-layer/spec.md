# Spec: Provider Layer

## Requirements

### PROV-001: Provider abstraction
The system MUST provide an `AIProvider` abstract base class.
- `AIProvider` MUST define `chat(model, messages, tools, stream=False) → ChatResponse`
- `AIProvider` MUST define `fetch_models() → list[ModelInfo]`
- `AIProvider` SHOULD define `chat_stream(model, messages, tools) → AsyncIterator[Token]`

### PROV-002: Bifrost provider
The system MUST provide a `BifrostProvider` that communicates with the Bifrost LLM Gateway.
- `BifrostProvider` MUST use OpenAI-compatible API at the configured base URL
- `BifrostProvider` MUST support virtual key routing via request headers or URL prefix
- `BifrostProvider` MUST implement `fetch_models()` by calling `GET /v1/models`
- `BifrostProvider` MUST support streaming via `stream=True` parameter
- `BifrostProvider` MUST support tool calling via the standard `tools` parameter
- `BifrostProvider` MUST NOT store API keys (keys live in Bifrost; connection is internal)

### PROV-003: Direct provider
The system SHOULD provide a `DirectProvider` for native provider access.
- `DirectProvider` MUST support at minimum: OpenAI, Anthropic, Google
- `DirectProvider` MUST use provider-specific SDKs or direct httpx calls
- `DirectProvider` MUST read API keys from environment variables (set by Salt pillar)
- `DirectProvider` SHOULD support provider-specific features not available via Bifrost

### PROV-004: Provider selection
The system MUST support per-model provider selection.
- `ai.model.provider_type` MUST switch between `bifrost` and `direct`
- When `provider_type = 'bifrost'`, the model name MUST use Bifrost's naming convention
- When `provider_type = 'direct'`, the model name MUST use the provider's native convention

### PROV-005: Model catalog sync
The system SHOULD support automatic model catalog synchronization.
- `BifrostProvider.fetch_models()` MUST return a list of `ModelInfo` with at minimum: id, provider, context_window, capabilities
- The system MAY create/update `ai.model` records from the fetched catalog
- Model capabilities MUST include: vision, tools, json_mode, streaming

### PROV-006: Retry and error handling
The system MUST implement retry logic for transient provider errors.
- HTTP 429 (rate limit) MUST trigger exponential backoff with jitter
- HTTP 5xx errors MUST retry up to 3 times
- HTTP 4xx errors (except 429) MUST NOT retry
- Retry configuration MUST be tunable per provider

## Non-requirements
- This spec does NOT cover embedding providers (separate spec)
- This spec does NOT cover image generation providers (separate spec)
