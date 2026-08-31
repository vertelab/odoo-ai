# -*- coding: utf-8 -*-
from __future__ import annotations
"""
Provider Layer — AIProvider (en klass, datadriven).

PROV-001: AIProvider — enda provider-klassen. All skillnad mellan
          leverantörer lagras på ai.provider-recordet:
            base_url    → endpoint (självhostad, gateway eller native)
            api_key     → nyckel på recordet (fylls i via UI)
            is_bifrost  → X-Virtual-Key-header (Bifrost-gateway)
            api_style   → openai (/chat/completions) | anthropic (/v1/messages)
PROV-006: Retry logic (exponential backoff on 429, max 3 on 5xx)

All providers speak the same interface:
    chat(model, messages, tools) → ChatResponse
    chat_stream(model, messages, tools) → AsyncIterator[TokenEvent]

No LangChain. No framework dependencies. Just httpx + tenacity.
"""

import json
import logging
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
# AIProvider — enda provider-klassen (PROV-001)
# ---------------------------------------------------------------------------

class AIProvider:
    """Enda provider-klassen — all skillnad är record-data.

    Konstrueras av resolve_provider_from_model från ett ai.provider-record
    (eller direkt för tester/fallback). Klassen vet inga leverantörsnamn,
    inga URL:er och inga env-var-namn — allt kommer in via parametrar.
    """

    def __init__(
        self,
        base_url: str = "",
        api_key: str = "",
        is_bifrost: bool = False,
        api_style: str = "openai",
        timeout: float = 120.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        if not self.api_key:
            _logger.warning("AIProvider: ingen API-nyckel på recordet.")

        self.is_bifrost = is_bifrost
        self.api_style = api_style or "openai"
        self.timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            headers = {"Content-Type": "application/json"}
            if self.is_bifrost:
                if self.api_key:
                    headers["X-Virtual-Key"] = self.api_key
            elif self.api_style == "anthropic":
                if self.api_key:
                    headers["x-api-key"] = self.api_key
                    headers["anthropic-version"] = "2023-06-01"
            elif self.api_key:
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
        """Send a chat completion request. Non-streaming."""
        if not model:
            # bifrost-client-provisioning D6: ingen provider/modell konfigurerad
            # är en FELSITUATION — aldrig tyst hårdkodad fallback-sträng.
            raise ProviderError(
                "No model configured: set ai_provider_* in odoo.conf or "
                "assign a model to the agent (ai_agent_core.default_model_id)."
            )
        if self.api_style == "anthropic":
            return await self._chat_anthropic(
                model, messages, tools, system_prompt, temperature, max_tokens
            )
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
        """Send a chat completion request. Streaming via async generator."""
        if not model:
            # D6: felsituation — ingen tyst hårdkodad fallback.
            raise ProviderError(
                "No model configured: set ai_provider_* in odoo.conf or "
                "assign a model to the agent (ai_agent_core.default_model_id)."
            )
        if self.api_style == "anthropic":
            async for event in self._stream_anthropic(
                model, messages, tools, system_prompt, temperature, max_tokens
            ):
                yield event
        else:
            async for event in self._stream_openai_compat(
                model, messages, tools, system_prompt, temperature, max_tokens
            ):
                yield event

    # -- Body builders --

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

    # -- HTTP helpers (retry + 400-debug + httpx 0.28-kompatibel) --

    @DEFAULT_RETRY
    async def _post(self, path: str, body: dict) -> dict:
        client = await self._get_client()
        url = f"{self.base_url}{path}"
        _logger.debug("AIProvider %s %s", path, body.get("model", "?"))
        response = await client.post(url, json=body)
        if response.is_error:
            # Inkludera response-body i felet (t.ex. 400-detaljer från providern)
            _detail = response.text[:500]
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as e:
                raise httpx.HTTPStatusError(
                    f"{e} — provider: {_detail}", request=e.request,
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
                    'provider 400-debug: model=%s msgs=%d tools=%d system_len=%d '
                    'roles=%s sample=%r ALLTOOLS=%s',
                    body.get('model'), len(body.get('messages', [])),
                    len(body.get('tools', [])),
                    len(body.get('system', '') or ''),
                    [m.get('role') for m in body.get('messages', [])][:20],
                    (body.get('messages') or [{}])[0].get('content', '')[:60],
                    [t.get('function', {}).get('name') for t in body.get('tools', [])])
                raise httpx.HTTPStatusError(
                    f"{e} — provider: {_detail}", request=e.request,
                    response=e.response) from e
        return response

    async def _post_stream(self, path: str, body: dict):
        client = await self._get_client()
        url = f"{self.base_url}{path}"
        body["stream"] = True
        _logger.debug("AIProvider stream %s %s", path, body.get("model", "?"))
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

    # -- OpenAI-compatible (OpenAI, DeepSeek, Cerebras, Groq, Google,
    #    OpenRouter, Ollama, custom) --

    async def _chat_openai_compat(self, model, messages, tools, system_prompt, temperature, max_tokens):
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

    async def _stream_openai_compat(self, model, messages, tools, system_prompt, temperature, max_tokens):
        body = self._build_openai_body(
            model, messages, tools, system_prompt, temperature, max_tokens
        )
        body["stream"] = True

        tool_call_buffer: dict[str, dict] = {}
        done_sent = False  # [DONE]-markören får inte ge en andra done

        async for chunk in self._post_stream("/chat/completions", body):
            if isinstance(chunk, TokenEvent):
                if chunk.type == "done":
                    # Flush eventuella tool_call-buffers innan done
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
                    tool_call_buffer.clear()
                    if done_sent:
                        continue
                    done_sent = True
                yield chunk
                continue

            choice = chunk.get("choices", [{}])[0]
            delta = choice.get("delta", {})

            # Reasoning/thinking (DeepSeek `reasoning_content`, OpenRouter
            # `reasoning`) — redovisa tänket precis som Pi visar det när det
            # ansluter direkt till bifrost. Kastas aldrig tyst.
            reasoning = delta.get("reasoning_content") or delta.get("reasoning")
            if reasoning:
                yield TokenEvent(type="thinking", token=reasoning)

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

            # Done (finish_reason i sista chunk:en)
            finish = choice.get("finish_reason")
            if finish and not delta.get("content") and not delta.get("tool_calls"):
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
                tool_call_buffer.clear()
                if not done_sent:
                    done_sent = True
                    yield TokenEvent(type="done", finish_reason=finish)

    # -- Anthropic-specific (api_style=anthropic) --

    async def _chat_anthropic(self, model, messages, tools, system_prompt, temperature, max_tokens):
        body = self._build_anthropic_body(model, messages, tools, system_prompt, temperature, max_tokens)
        data = await self._post("/messages", body)

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
        body = self._build_anthropic_body(model, messages, tools, system_prompt, temperature, max_tokens)
        body["stream"] = True

        current_tool: dict | None = None
        response = await self._stream_open(f"{self.base_url}/messages", body)
        try:
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
        finally:
            await response.aclose()


# ---------------------------------------------------------------------------
# Provider resolution (PROV-007) — datadriven, inget registry/enum
# ---------------------------------------------------------------------------

def resolve_provider_from_model(ai_model):
    """Resolve a provider instance from an ai.model record.

    Walks ai.model -> ai.provider; allt (base_url, api_key, is_bifrost,
    api_style) läses från recordet. Inga hårdkodade providers.

    Args:
        ai_model: An ai.model record (browse object)

    Returns:
        AIProvider instance, or None if unresolvable
    """
    if not ai_model:
        return None

    provider = ai_model.provider
    if not provider:
        _logger.warning("Model %s has no provider configured", ai_model.name)
        return None

    return AIProvider(
        base_url=provider.base_url or '',
        api_key=provider.api_key or '',
        is_bifrost=bool(getattr(provider, 'is_bifrost', False)),
        api_style=provider.api_style or 'openai',
        timeout=provider.timeout or 120.0,
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


def get_default_model_name():
    """Namnet på default-modellen (ai_agent_core.default_model_id).

    Används som fallback när en agent saknar modell — aldrig hårdkodade
    leverantörsnamn. Returnerar '' om ingen default är konfigurerad.
    """
    _provider, model = get_default_provider()
    # Wire-semantik: api_name om satt, annars name (bakåtkompatibelt).
    return (model.api_name or model.name) if model else ''


def get_cheapest_model():
    """Billigaste aktiva modellen — datadrivet (bifrost-client-provisioning D5).

    Lägst sys_multiplier (Vertels kostnadsproxy), tie-break lägst
    cost_input_1k sedan id. Aldrig hårdkodad modellsträng.

    Returns:
        ai.model record eller tom records
    """
    try:
        from odoo.http import request

        if not request:
            return None
        return request.env['ai.model'].sudo().search([
            ('active', '=', True),
            ('status', '=', 'active'),
        ], order='sys_multiplier asc, cost_input_1k asc, id asc', limit=1)
    except Exception:
        return None


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
