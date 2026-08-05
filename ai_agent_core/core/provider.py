# -*- coding: utf-8 -*-
from __future__ import annotations
"""
Provider Layer — AIProvider ABC + BifrostProvider + DirectProvider.

PROV-001: AIProvider abstract base class
PROV-002: BifrostProvider (OpenAI-compatible gateway)
PROV-003: DirectProvider (native OpenAI, Anthropic, DeepSeek)
PROV-006: Retry logic (exponential backoff on 429, max 3 on 5xx)

All providers speak the same interface:
    chat(model, messages, tools) → ChatResponse
    chat_stream(model, messages, tools) → AsyncIterator[TokenEvent]

No LangChain. No framework dependencies. Just httpx + tenacity.
"""

import asyncio
import json
import logging
import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import AsyncIterator, Optional

import httpx
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception,
)

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


class Provider(Enum):
    """Supported direct providers."""
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    DEEPSEEK = "deepseek"
    GOOGLE = "google"
    CEREBRAS = "cerebras"
    GROQ = "groq"


class Role(Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


@dataclass
class Message:
    role: Role
    content: str
    tool_call_id: Optional[str] = None
    tool_calls: Optional[list[dict]] = None
    name: Optional[str] = None
    # Display-only sidecars — stripped before sending to providers
    _display: Optional[dict] = None  # UI metadata (counts, filter info, etc.)
    source: Optional[dict] = None    # Connector/source framing
    ts: Optional[float] = None        # Append timestamp (unix seconds)

    def to_openai(self) -> dict:
        """Serialize to OpenAI format. Sidecars are stripped."""
        msg = {"role": self.role.value, "content": self.content}
        if self.tool_call_id:
            msg["tool_call_id"] = self.tool_call_id
        if self.tool_calls:
            msg["tool_calls"] = self.tool_calls
        if self.name:
            msg["name"] = self.name
        return msg

    def to_anthropic(self) -> dict:
        role_map = {
            Role.USER: "user",
            Role.ASSISTANT: "assistant",
        }
        if self.role == Role.SYSTEM:
            return None  # Anthropic handles system separately
        if self.role == Role.TOOL:
            return {
                "role": "user",
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": self.tool_call_id,
                    "content": self.content,
                }],
            }
        content = self.content
        if self.tool_calls:
            content = [
                {"type": "text", "text": self.content} if self.content else None,
                *[{
                    "type": "tool_use",
                    "id": tc["id"],
                    "name": tc["function"]["name"],
                    "input": json.loads(tc["function"]["arguments"]),
                } for tc in self.tool_calls],
            ]
            content = [c for c in content if c is not None]
        return {"role": role_map[self.role], "content": content}


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class ChatResponse:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""
    finish_reason: str = "stop"
    needs_clarification: bool = False  # HITL-007: agent needs to ask a question
    clarification_question: str = ""  # The question to ask the user


@dataclass
class TokenEvent:
    """Streaming token event."""
    type: str  # "token", "tool_call_start", "tool_call_delta", "tool_call_end", "done"
    token: str = ""
    tool_call: Optional[ToolCall] = None
    finish_reason: str = ""


class ProviderError(Exception):
    """Provider-level error with retry information."""
    def __init__(self, message: str, status_code: int = 0, retryable: bool = False):
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable


# ---------------------------------------------------------------------------
# Retry logic (PROV-006)
# ---------------------------------------------------------------------------

def _is_retryable(exception: Exception) -> bool:
    if isinstance(exception, ProviderError):
        return exception.retryable
    if isinstance(exception, httpx.HTTPStatusError):
        code = exception.response.status_code
        return code == 429 or code >= 500
    if isinstance(exception, (httpx.ConnectError, httpx.TimeoutException)):
        return True
    return False


DEFAULT_RETRY = retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=30),
    retry=retry_if_exception(_is_retryable),
    reraise=True,
)


# ---------------------------------------------------------------------------
# AIProvider ABC (PROV-001)
# ---------------------------------------------------------------------------

class AIProvider(ABC):
    """Abstract base for all LLM providers."""

    @abstractmethod
    async def chat(
        self,
        model: str,
        messages: list[Message],
        tools: Optional[list[dict]] = None,
        system_prompt: str = "",
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> ChatResponse:
        """Send a chat completion request. Non-streaming."""
        ...

    @abstractmethod
    async def chat_stream(
        self,
        model: str,
        messages: list[Message],
        tools: Optional[list[dict]] = None,
        system_prompt: str = "",
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> AsyncIterator[TokenEvent]:
        """Send a chat completion request. Streaming via async generator."""
        ...


# ---------------------------------------------------------------------------
# BifrostProvider (PROV-002)
# ---------------------------------------------------------------------------

class BifrostProvider(AIProvider):
    """OpenAI-compatible provider via Bifrost LLM Gateway.

    Bifrost handles:
    - API key management
    - Load balancing (weighted routing)
    - Provider-specific quirks
    - Model catalog

    We just speak OpenAI format to a single endpoint.
    """

    def __init__(
        self,
        base_url: str = "http://192.168.11.150:8080/v1",
        virtual_key: str = "opencode",
        timeout: float = 120.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.virtual_key = virtual_key
        self.timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout),
                headers={
                    "Content-Type": "application/json",
                    "X-Virtual-Key": self.virtual_key,
                },
            )
        return self._client

    @DEFAULT_RETRY
    async def _post(self, path: str, body: dict) -> dict:
        client = await self._get_client()
        url = f"{self.base_url}{path}"
        _logger.debug("Bifrost %s %s", path, body.get("model", "?"))
        response = await client.post(url, json=body)
        if response.is_error:
            # Inkludera response-body i felet (t.ex. 400-detaljer från providern)
            _detail = response.text[:500]
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as e:
                raise httpx.HTTPStatusError(
                    f"{e} — Bifrost: {_detail}", request=e.request,
                    response=e.response) from e
        return response.json()

    @DEFAULT_RETRY
    async def _stream_open(self, url: str, body: dict):
        """Öppna streaming-anrop med retry (429/5xx/connect).

        Returnerar en öppen httpx-response redo att itereras. Vid statusfel
        stängs anslutningen innan raise, så retry får en fräsch request.
        """
        client = await self._get_client()
        request = client.build_request('POST', url, json=body)
        response = await client.send(request, stream=True)
        if response.is_error:
            await response.aread()  # läs body innan .text (streaming)
            _detail = response.text[:500]
            await response.aclose()
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as e:
                _logger.error(
                    'Bifrost 400-debug: model=%s msgs=%d tools=%d system_len=%d '
                    'roles=%s sample=%r ALLTOOLS=%s',
                    body.get('model'), len(body.get('messages', [])),
                    len(body.get('tools', [])),
                    len(body.get('system', '') or ''),
                    [m.get('role') for m in body.get('messages', [])][:20],
                    (body.get('messages') or [{}])[0].get('content', '')[:60],
                    [t.get('function', {}).get('name') for t in body.get('tools', [])])
                raise httpx.HTTPStatusError(
                    f"{e} — Bifrost: {_detail}", request=e.request,
                    response=e.response) from e
        return response

    async def _post_stream(self, path: str, body: dict):
        client = await self._get_client()
        url = f"{self.base_url}{path}"
        body["stream"] = True
        _logger.debug("Bifrost stream %s %s", path, body.get("model", "?"))
        response = await self._stream_open(url, body)
        # httpx 0.28: Response saknar __aenter__ — stäng explicit i finally
        try:
            async for line in response.aiter_lines():
                if line.startswith("data: "):
                    data_str = line[6:]
                    if data_str == "[DONE]":
                        yield TokenEvent(type="done", finish_reason="stop")
                        return
                    try:
                        yield json.loads(data_str)
                    except json.JSONDecodeError:
                        continue
        finally:
            await response.aclose()

    @staticmethod
    def _build_openai_body(
        model: str,
        messages: list[Message],
        tools: Optional[list[dict]],
        system_prompt: str,
        temperature: float,
        max_tokens: int,
    ) -> dict:
        msgs = []
        if system_prompt:
            msgs.append({"role": "system", "content": system_prompt})
        msgs.extend(m.to_openai() for m in messages)

        body = {
            "model": model,
            "messages": msgs,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            body["tools"] = tools
        return body

    async def chat(
        self,
        model: str,
        messages: list[Message],
        tools: Optional[list[dict]] = None,
        system_prompt: str = "",
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> ChatResponse:
        body = self._build_openai_body(
            model, messages, tools, system_prompt, temperature, max_tokens
        )
        data = await self._post("/chat/completions", body)

        choice = data["choices"][0]
        msg = choice.get("message", {})

        tool_calls = []
        if msg.get("tool_calls"):
            for tc in msg["tool_calls"]:
                tool_calls.append(ToolCall(
                    id=tc["id"],
                    name=tc["function"]["name"],
                    arguments=json.loads(tc["function"]["arguments"]),
                ))

        return ChatResponse(
            text=msg.get("content", "") or "",
            tool_calls=tool_calls,
            input_tokens=data.get("usage", {}).get("prompt_tokens", 0),
            output_tokens=data.get("usage", {}).get("completion_tokens", 0),
            model=data.get("model", model),
            finish_reason=choice.get("finish_reason", "stop"),
        )

    async def chat_stream(
        self,
        model: str,
        messages: list[Message],
        tools: Optional[list[dict]] = None,
        system_prompt: str = "",
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> AsyncIterator[TokenEvent]:
        body = self._build_openai_body(
            model, messages, tools, system_prompt, temperature, max_tokens
        )

        tool_call_buffer: dict[str, dict] = {}
        done_sent = False  # [DONE]-markören får inte ge en andra done

        async for chunk in self._post_stream("/chat/completions", body):
            if isinstance(chunk, TokenEvent):
                if chunk.type == "done":
                    if done_sent:
                        continue
                    done_sent = True
                yield chunk
                continue

            choice = chunk.get("choices", [{}])[0]
            delta = choice.get("delta", {})

            # Text token
            if delta.get("content"):
                yield TokenEvent(type="token", token=delta["content"])

            # Tool call start
            if delta.get("tool_calls"):
                for tc in delta["tool_calls"]:
                    idx = tc.get("index", 0)
                    tid = tc.get("id")
                    if tid and idx not in tool_call_buffer:
                        tool_call_buffer[idx] = {
                            "id": tid,
                            "name": tc["function"]["name"],
                            "arguments": "",
                        }
                        yield TokenEvent(type="tool_call_start", tool_call=ToolCall(
                            id=tid,
                            name=tc["function"]["name"],
                            arguments={},
                        ))
                    if "function" in tc and "arguments" in tc["function"]:
                        tool_call_buffer.setdefault(idx, {})["arguments"] = (
                            tool_call_buffer.get(idx, {}).get("arguments", "")
                            + tc["function"]["arguments"]
                        )

            # Done
            finish = choice.get("finish_reason")
            if finish:
                for idx, buf in tool_call_buffer.items():
                    try:
                        buf["arguments"] = json.loads(buf["arguments"])
                    except json.JSONDecodeError:
                        pass
                    yield TokenEvent(type="tool_call_end", tool_call=ToolCall(
                        id=buf["id"],
                        name=buf["name"],
                        arguments=buf["arguments"] if isinstance(buf["arguments"], dict) else {},
                    ))
                if not done_sent:
                    done_sent = True
                    yield TokenEvent(type="done", finish_reason=finish)


# ---------------------------------------------------------------------------
# DirectProvider (PROV-003)
# ---------------------------------------------------------------------------

class DirectProvider(AIProvider):
    """Native provider access for features Bifrost doesn't proxy.

    Uses provider-specific APIs directly.
    API keys from environment variables (set by Salt pillar).
    """

    # Environment variable names per provider
    API_KEY_ENV = {
        Provider.OPENAI: "OPENAI_API_KEY",
        Provider.ANTHROPIC: "ANTHROPIC_API_KEY",
        Provider.DEEPSEEK: "DEEPSEEK_API_KEY",
        Provider.GOOGLE: "GOOGLE_API_KEY",
        Provider.CEREBRAS: "CEREBRAS_API_KEY",
        Provider.GROQ: "GROQ_API_KEY",
    }

    # Base URLs per provider
    BASE_URLS = {
        Provider.OPENAI: "https://api.openai.com/v1",
        Provider.ANTHROPIC: "https://api.anthropic.com/v1",
        Provider.DEEPSEEK: "https://api.deepseek.com/v1",
        Provider.GOOGLE: "https://generativelanguage.googleapis.com/v1beta",
        Provider.CEREBRAS: "https://api.cerebras.ai/v1",
        Provider.GROQ: "https://api.groq.com/openai/v1",
    }

    def __init__(
        self,
        provider: Provider,
        api_key: str = "",
        timeout: float = 120.0,
    ):
        self.provider = provider
        self.api_key = api_key or os.environ.get(self.API_KEY_ENV.get(provider, ""), "")
        if not self.api_key:
            _logger.warning(
                "DirectProvider(%s): no API key found. Set %s env var.",
                provider.value, self.API_KEY_ENV.get(provider, "?"),
            )
        self.base_url = self.BASE_URLS[provider]
        self.timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            headers = {"Content-Type": "application/json"}
            if self.provider == Provider.ANTHROPIC:
                headers["x-api-key"] = self.api_key
                headers["anthropic-version"] = "2023-06-01"
            else:
                headers["Authorization"] = f"Bearer {self.api_key}"
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout),
                headers=headers,
            )
        return self._client

    async def chat(
        self,
        model: str,
        messages: list[Message],
        tools: Optional[list[dict]] = None,
        system_prompt: str = "",
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> ChatResponse:
        if self.provider == Provider.ANTHROPIC:
            return await self._chat_anthropic(
                model, messages, tools, system_prompt, temperature, max_tokens
            )
        else:
            return await self._chat_openai_compat(
                model, messages, tools, system_prompt, temperature, max_tokens
            )

    async def chat_stream(
        self,
        model: str,
        messages: list[Message],
        tools: Optional[list[dict]] = None,
        system_prompt: str = "",
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> AsyncIterator[TokenEvent]:
        if self.provider == Provider.ANTHROPIC:
            async for event in self._stream_anthropic(
                model, messages, tools, system_prompt, temperature, max_tokens
            ):
                yield event
        else:
            async for event in self._stream_openai_compat(
                model, messages, tools, system_prompt, temperature, max_tokens
            ):
                yield event

    # -- OpenAI-compatible (works for OpenAI, DeepSeek, Cerebras, Groq) --

    @DEFAULT_RETRY
    async def _chat_openai_compat(self, model, messages, tools, system_prompt, temperature, max_tokens):
        client = await self._get_client()
        body = BifrostProvider._build_openai_body(
            BifrostProvider.__new__(BifrostProvider),  # reuse the body builder
            model, messages, tools, system_prompt, temperature, max_tokens,
        )
        url = f"{self.base_url}/chat/completions"
        response = await client.post(url, json=body)
        response.raise_for_status()
        data = response.json()

        choice = data["choices"][0]
        msg = choice.get("message", {})
        tool_calls = []
        if msg.get("tool_calls"):
            for tc in msg["tool_calls"]:
                tool_calls.append(ToolCall(
                    id=tc["id"],
                    name=tc["function"]["name"],
                    arguments=json.loads(tc["function"]["arguments"]),
                ))
        return ChatResponse(
            text=msg.get("content", "") or "",
            tool_calls=tool_calls,
            input_tokens=data.get("usage", {}).get("prompt_tokens", 0),
            output_tokens=data.get("usage", {}).get("completion_tokens", 0),
            model=data.get("model", model),
            finish_reason=choice.get("finish_reason", "stop"),
        )

    async def _stream_openai_compat(self, model, messages, tools, system_prompt, temperature, max_tokens):
        client = await self._get_client()
        body = BifrostProvider._build_openai_body(
            BifrostProvider.__new__(BifrostProvider),
            model, messages, tools, system_prompt, temperature, max_tokens,
        )
        body["stream"] = True
        url = f"{self.base_url}/chat/completions"

        tool_call_buffer: dict[str, dict] = {}
        async with client.stream("POST", url, json=body) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if line.startswith("data: "):
                    data_str = line[6:]
                    if data_str == "[DONE]":
                        for idx, buf in tool_call_buffer.items():
                            try:
                                buf["arguments"] = json.loads(buf["arguments"])
                            except json.JSONDecodeError:
                                pass
                            yield TokenEvent(type="tool_call_end", tool_call=ToolCall(
                                id=buf["id"], name=buf["name"],
                                arguments=buf["arguments"] if isinstance(buf["arguments"], dict) else {},
                            ))
                        yield TokenEvent(type="done", finish_reason="stop")
                        return
                    try:
                        chunk = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue
                    choice = chunk.get("choices", [{}])[0]
                    delta = choice.get("delta", {})
                    if delta.get("content"):
                        yield TokenEvent(type="token", token=delta["content"])
                    if delta.get("tool_calls"):
                        for tc in delta["tool_calls"]:
                            idx = tc.get("index", 0)
                            tid = tc.get("id")
                            if tid and idx not in tool_call_buffer:
                                tool_call_buffer[idx] = {
                                    "id": tid,
                                    "name": tc["function"]["name"],
                                    "arguments": "",
                                }
                                yield TokenEvent(type="tool_call_start", tool_call=ToolCall(
                                    id=tid, name=tc["function"]["name"], arguments={},
                                ))
                            if "function" in tc and "arguments" in tc["function"]:
                                tool_call_buffer.setdefault(idx, {})["arguments"] = (
                                    tool_call_buffer.get(idx, {}).get("arguments", "")
                                    + tc["function"]["arguments"]
                                )
                    finish = choice.get("finish_reason")
                    if finish and not delta.get("content") and not delta.get("tool_calls"):
                        for idx, buf in tool_call_buffer.items():
                            try:
                                buf["arguments"] = json.loads(buf["arguments"])
                            except json.JSONDecodeError:
                                pass
                            yield TokenEvent(type="tool_call_end", tool_call=ToolCall(
                                id=buf["id"], name=buf["name"],
                                arguments=buf["arguments"] if isinstance(buf["arguments"], dict) else {},
                            ))
                        yield TokenEvent(type="done", finish_reason=finish)

    # -- Anthropic-specific --

    @DEFAULT_RETRY
    async def _chat_anthropic(self, model, messages, tools, system_prompt, temperature, max_tokens):
        client = await self._get_client()
        body = self._build_anthropic_body(model, messages, tools, system_prompt, temperature, max_tokens)
        response = await client.post(f"{self.base_url}/messages", json=body)
        response.raise_for_status()
        data = response.json()

        text = ""
        tool_calls = []
        for block in data.get("content", []):
            if block["type"] == "text":
                text += block["text"]
            elif block["type"] == "tool_use":
                tool_calls.append(ToolCall(
                    id=block["id"],
                    name=block["name"],
                    arguments=block["input"],
                ))
        return ChatResponse(
            text=text,
            tool_calls=tool_calls,
            input_tokens=data.get("usage", {}).get("input_tokens", 0),
            output_tokens=data.get("usage", {}).get("output_tokens", 0),
            model=data.get("model", model),
            finish_reason=data.get("stop_reason", "end_turn"),
        )

    async def _stream_anthropic(self, model, messages, tools, system_prompt, temperature, max_tokens):
        client = await self._get_client()
        body = self._build_anthropic_body(model, messages, tools, system_prompt, temperature, max_tokens)
        body["stream"] = True

        current_tool: dict | None = None
        async with client.stream("POST", f"{self.base_url}/messages", json=body) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue
                try:
                    event = json.loads(line[6:])
                except json.JSONDecodeError:
                    continue

                ev_type = event.get("type", "")
                if ev_type == "content_block_delta":
                    delta = event.get("delta", {})
                    if delta.get("type") == "text_delta":
                        yield TokenEvent(type="token", token=delta["text"])
                    elif delta.get("type") == "input_json_delta":
                        if current_tool:
                            current_tool["arguments"] = (
                                current_tool.get("arguments", "") + delta["partial_json"]
                            )
                elif ev_type == "content_block_start":
                    block = event.get("content_block", {})
                    if block.get("type") == "tool_use":
                        current_tool = {
                            "id": block["id"],
                            "name": block["name"],
                            "arguments": "",
                        }
                        yield TokenEvent(type="tool_call_start", tool_call=ToolCall(
                            id=block["id"], name=block["name"], arguments={},
                        ))
                elif ev_type == "content_block_stop":
                    if current_tool:
                        try:
                            args = json.loads(current_tool["arguments"])
                        except json.JSONDecodeError:
                            args = {}
                        yield TokenEvent(type="tool_call_end", tool_call=ToolCall(
                            id=current_tool["id"],
                            name=current_tool["name"],
                            arguments=args,
                        ))
                        current_tool = None
                elif ev_type == "message_stop":
                    yield TokenEvent(type="done", finish_reason="end_turn")

    def _build_anthropic_body(self, model, messages, tools, system_prompt, temperature, max_tokens):
        system = None
        if system_prompt:
            system = system_prompt

        anthropic_msgs = []
        for m in messages:
            converted = m.to_anthropic()
            if converted:
                anthropic_msgs.append(converted)

        body = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": anthropic_msgs,
        }
        if system:
            body["system"] = system
        if tools:
            body["tools"] = self._tools_to_anthropic(tools)
        return body

    @staticmethod
    def _tools_to_anthropic(tools: list[dict]) -> list[dict]:
        """Convert OpenAI tool format to Anthropic format."""
        result = []
        for t in tools:
            fn = t.get("function", t)
            result.append({
                "name": fn["name"],
                "description": fn.get("description", ""),
                "input_schema": fn.get("parameters", {"type": "object", "properties": {}}),
            })
        return result


# ---------------------------------------------------------------------------
# Provider Registry & Factory (PROV-007)
# ---------------------------------------------------------------------------

PROVIDER_REGISTRY = {
    'bifrost': BifrostProvider,
    'direct': DirectProvider,
    'openai': DirectProvider,
    'openrouter': DirectProvider,
    'anthropic': DirectProvider,
    'deepseek': DirectProvider,
    'google': DirectProvider,
    'cerebras': DirectProvider,
    'groq': DirectProvider,
    'custom': DirectProvider,
}


def resolve_provider_from_model(ai_model):
    """Resolve a provider instance from an ai.model record.

    Walks ai.model -> ai.provider to get base_url, api_key, provider_type,
    then instantiates the correct provider class.

    Args:
        ai_model: An ai.model record (browse object)

    Returns:
        AIProvider instance configured with the provider's base_url and api_key
    """
    if not ai_model:
        return None

    provider = ai_model.provider_id
    if not provider:
        _logger.warning("Model %s has no provider configured", ai_model.name)
        return None

    provider_type = provider.provider_type or 'custom'
    ProviderClass = PROVIDER_REGISTRY.get(provider_type)

    if not ProviderClass:
        _logger.warning(
            "Unknown provider type '%s' for model %s. "
            "Available: %s",
            provider_type, ai_model.name,
            ', '.join(PROVIDER_REGISTRY.keys()),
        )
        return None

    base_url = provider.base_url or ''
    api_key = provider.api_key or ''

    # Handle provider-specific constructor params
    if provider_type == 'bifrost':
        # Bifrost uses virtual_key instead of api_key at contructor level
        return ProviderClass(
            base_url=base_url,
            virtual_key=api_key or 'opencode',
        )
    elif provider_type == 'direct' or provider_type == 'custom':
        # DirectProvider takes a Provider enum + api_key
        provider_enum_name = (provider.provider_type or 'openai').upper()
        provider_enum = getattr(Provider, provider_enum_name, Provider.OPENAI)
        return ProviderClass(
            provider=provider_enum,
            api_key=api_key,
        )
    else:
        # OpenAI-compatible (openai, anthropic, openrouter, deepseek, google, etc.)
        return ProviderClass(
            base_url=base_url,
            api_key=api_key,
        )


def resolve_provider_from_coworker(coworker, agent_rel=None):
    """Resolve provider from a coworker's agent chain.

    Walks: ai.coworker -> ai.coworker.agent -> ai.agent -> ai.model -> ai.provider

    Args:
        coworker: An ai.coworker browse record
        agent_rel: Optional specific ai.coworker.agent record.
                   If None, uses the first agent.

    Returns:
        (AIProvider, ai.model) tuple, or (None, None) if unresolvable
    """
    if not coworker:
        return None, None

    if agent_rel:
        agent = agent_rel.agent_id
    elif coworker.agent_ids:
        agent = coworker.agent_ids[0].agent_id
    else:
        return None, None

    if not agent or not agent.model_id:
        return None, None

    provider = resolve_provider_from_model(agent.model_id)
    return provider, agent.model_id


def get_default_provider():
    """Get the default provider from system parameters.

    Uses ir.config_parameter 'ai_agent_core.default_model_id' to
    look up an ai.model record. Returns (provider, model) or (None, None).
    """
    try:
        from odoo.http import request

        if not request:
            return None, None
        param = request.env['ir.config_parameter'].sudo().get_param(
            'ai_agent_core.default_model_id'
        )
        if not param:
            return None, None

        model = request.env['ai.model'].sudo().browse(int(param))
        if not model.exists():
            return None, None

        return resolve_provider_from_model(model), model
    except Exception:
        return None, None


class ProviderFactory:
    """Factory for creating provider instances from Odoo model records.

    Usage:
        provider, model = ProviderFactory.from_coworker(coworker)
        provider, model = ProviderFactory.from_agent_rel(agent_rel)
    """

    @staticmethod
    def from_coworker(coworker):
        """Resolve provider from a coworker's first agent."""
        return resolve_provider_from_coworker(coworker)

    @staticmethod
    def from_agent_rel(agent_rel):
        """Resolve provider from a specific agent assignment."""
        return resolve_provider_from_coworker(
            agent_rel.coworker_id, agent_rel=agent_rel
        )

    @staticmethod
    def from_model(ai_model):
        """Resolve provider from an ai.model record directly."""
        return resolve_provider_from_model(ai_model), ai_model

    @staticmethod
    def from_supervisor_agents(coworker):
        """Resolve providers for all agents in supervisor mode.

        Returns:
            list of (ai.coworker.agent, AIProvider, ai.model) tuples
        """
        result = []
        for agent_rel in coworker.agent_ids:
            provider, model = resolve_provider_from_coworker(
                coworker, agent_rel=agent_rel
            )
            result.append((agent_rel, provider, model))
        return result
