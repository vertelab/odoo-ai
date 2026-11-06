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

No LangChain. No framework dependencies. Just httpx.
"""

import asyncio
import json
import logging
import random
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from enum import Enum
from typing import AsyncIterator, Optional

import httpx

_logger = logging.getLogger(__name__)


#: Provider-alias → den typade vokabulären (utfall-och-tokenmatning D3).
#: Anthropic säger `end_turn`/`stop_sequence`; OpenAI säger `stop`. Sessionens
#: finish_reason är en Selection, så aliasen måste normaliseras vid gränsen —
#: annars sparas ett värde som inte finns i vokabulären.
_FINISH_ALIASES = {
    'end_turn': 'stop',
    'stop_sequence': 'stop',
    'function_call': 'tool_calls',
    'completed': 'stop',
}


def _normalize_finish_reason(value):
    """Mappa en providers stop_reason till sessionens typade vokabulär."""
    v = (value or '').strip()
    return _FINISH_ALIASES.get(v, v or 'stop')


def _safe_json_loads(text, default=None):
    """Tolerant JSON-parsing av verktygsarguments.

    Bifrost/OpenRouter-modeller returnerar ibland arguments med enkla
    citattecken, nyrader eller extra tecken → std json.loads kastar
    JSONDecodeError. Fallback: försök ast.literal_eval (icke-standard-enkla
    citat), annars returnera en dict {p: text} eller default.
    """
    if text is None:
        return default or {}
    s = text.strip()
    if not s:
        return default or {}
    try:
        return json.loads(s)
    except Exception:
        pass  # best-effort: tyst fallback — se etiketteringsregeln
    try:
        import ast
        return ast.literal_eval(s)
    except Exception:
        pass  # best-effort: tyst fallback — se etiketteringsregeln
    # Sista utväg: om det är ett platt "key: value"-mönster, wrap:a
    if default is not None:
        return default
    return {"_raw": s}

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

    def __post_init__(self):
        """Gör `role` till en Role-medlem, oavsett vad anroparen skickade.

        VARFÖR (fix-message-role-string): `@dataclass` validerar inte
        fälttyper. `Message(role='user')` gick därför igenom konstruktionen,
        och kraschade först i `to_openai()`:

            AttributeError: 'str' object has no attribute 'value'

        Sju anropsplatser gjorde detta (namngenerering, onboarding,
        webbplats-RAG, personligt minne, koncept-extraktion). Alla fångade
        undantaget och loggade en varning — därför var felet tyst. Symptomet
        som syntes var att sessionsnamnen aldrig blev berättande:

            WARNING ai_session: Namn: generering misslyckades för session
            27912: 'str' object has no attribute 'value'

        Normaliseringen ligger här och inte bara på anropsplatserna: skyddet
        ska ligga där felet uppstår, så en framtida anropsplats inte kan
        återinföra det (samma resonemang som AgentConfig.__post_init__,
        fix-stream-max-rounds-none).

        Ett OKÄNT rollvärde nekas — ett meddelande med fel roll ger ett svar
        som ser rimligt ut men är byggt på fel indata, vilket är värre än ett
        tydligt fel vid konstruktionen.
        """
        if isinstance(self.role, str):
            try:
                self.role = Role(self.role)
            except ValueError:
                raise ValueError(
                    'Message.role=%r är inte en Role-medlem (giltiga: %s)'
                    % (self.role,
                       ', '.join(sorted(r.value for r in Role))))
            # debug, inte warning: sju anropsplatser × varje LLM-anrop
            # hade fyllt loggen. Konverteringen syns när någon letar.
            _logger.debug(
                'Message.role var en sträng — normaliserade till %s',
                self.role.name)

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
                    "input": _safe_json_loads(tc["function"]["arguments"]),
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
    # Multi-agent (supervisor/conference): per-agent token & model usage att
    # föra vidare till session-persist (Väg A) — sparas som egna meddelande-
    # rader per agent så tokens kan summeras per modell. Varje element:
    #   {'agent': <str/name>, 'model': <str/model_real>, 'input_tokens': int,
    #    'output_tokens': int}
    agent_usage: list = field(default_factory=list)
    # Per-varv-räkning (webui-show-work-again 4.1). `input_tokens` är summan
    # över varven — varje verktygsrunda skickar hela historiken igen. Dessa
    # bär uppdelningen så en summa på sex siffror går att förstå.
    rounds: int = 0
    round_inputs: list = field(default_factory=list)


@dataclass
class TokenEvent:
    """Streaming token event."""
    # "token", "thinking", "tool_call_start", "tool_call_delta",
    # "tool_call_end", "done", "retry"
    type: str
    token: str = ""
    tool_call: Optional[ToolCall] = None
    finish_reason: str = ""
    # Verklig token-usage (providerns usage-rapport). Fylls i av
    # stream-generatorerna (include_usage/message_start/message_delta) och
    # aggregeras av StreamingAgentLoop till totaler på slutgiltiga done.
    # 0 = ej rapporterad (klienten får estimera).
    input_tokens: int = 0
    output_tokens: int = 0
    # Fel som inträffade när ett avslutande/sammanfattande anrop gjordes.
    # Sätts på den slutgiltiga done-händelsen så att anroparen kan skriva ett
    # ärligt avslut och sätta avslutsorsak i stället för att turen tystnar
    # (web-ui-stream-turn-persistens 1.1). Tom sträng = inget fel.
    error: str = ""
    # provider-transient-retry D5: ett synligt "försöker igen"-event.
    # `attempt` är det försök som just misslyckades (1-baserat), `reason`
    # är den korta orsaksetiketten. Innehåller aldrig svarstext — klienten
    # ska kunna visa väntan utan att lägga något till svaret.
    attempt: int = 0
    reason: str = ""
    # Per-varv-räkning (webui-show-work-again 4.1). En tur som gör flera
    # verktygsrundor skickar hela historiken igen vid varje varv, så
    # `input_tokens` på done är en SUMMA över varven — inte en enda prompt.
    # `rounds` är antalet varv, `round_inputs` varje varvs input i ordning.
    # Sätts på det slutgiltiga done-eventet av StreamingAgentLoop.
    rounds: int = 0
    round_inputs: list = None


class ProviderError(Exception):
    """Provider-level error with retry information."""
    def __init__(self, message: str, status_code: int = 0, retryable: bool = False):
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable


class ProviderRetriesExhausted(ProviderError):
    """Alla försök uttömda — felet bär orsak och antal försök.

    Skiljer "gatewayen sa nej" (avslag) från "vi försökte N gånger och gav
    upp". Utan den skillnaden ser ett uttömt retry ut som ett generiskt
    anslutningsfel och användaren får ingen ledtråd om att det var
    rate limit som gällde.
    """

    def __init__(self, message: str, status_code: int = 0, attempts: int = 1,
                 last_reason: str = ''):
        super().__init__(message, status_code=status_code, retryable=False)
        self.attempts = attempts
        self.last_reason = last_reason


class GatewayRejection(ProviderError):
    """Gatewayen avvisade anropet med ett strukturerat fel.

    Skiljer ett *avslag* (modellen är inte entitled / finns inte) från ett
    *anslutningsfel* (timeout, DNS). Utan denna skillnad visades
    "Anslutningen till AI-servern bröts" för ett 403 `combo_forbidden` —
    användaren fick ingen ledtråd och coworkern gissade fel orsak.

    Fälten bär vad användaren behöver för att förstå och åtgärda:
      model  — modellnamnet som avvisades
      status — HTTP-statusen (403, 404, …)
      reason — gatewayens egen orsakstyp (t.ex. 'combo_forbidden')
    """

    def __init__(self, model: str, status_code: int, reason: str,
                 detail: str = ''):
        self.model = model or '?'
        self.reason = reason or 'rejected'
        self.detail = detail
        if self.reason == 'combo_forbidden':
            msg = (
                "Modellen '%s' är inte tillgänglig för denna nyckel "
                "(gateway-avslag: %s). Kontrollera att modellnamnet finns i "
                "gatewayens katalog och att nyckeln är entitled till det."
                % (self.model, self.reason))
        elif self.reason in ('model_not_found', 'not_found'):
            msg = (
                "Modellen '%s' finns inte hos leverantören "
                "(gateway-avslag: %s)." % (self.model, self.reason))
        else:
            msg = (
                "Gatewayen avvisade modellen '%s' (HTTP %s, %s)."
                % (self.model, status_code, self.reason))
        if detail:
            msg += ' Gateway: %s' % detail[:300]
        super().__init__(msg, status_code=status_code, retryable=False)


def _is_stream_options_rejection(exception: "httpx.HTTPStatusError") -> bool:
    """Gäller 400:an att providern inte accepterar stream_options?

    Skiljer den ENDA 400 som retryen i _iter_stream_retry är till för
    ("stream_options/include_usage stöds inte") från alla andra 400:or
    (saldofel, okänd provider, modellfel ...). Utan denna kontroll
    retryades varje 400 blint — se _iter_stream_retry.

    Läser provider-texten ur exceptionens meddelande: _stream_open bäddar
    in `response.text[:500]` som "... — provider: {_detail}", och
    response-body är redan stängd när vi kommer hit.

    Fail-safe: kan texten inte läsas returneras False — vi retryar hellre
    inte än att göra ett onödigt anrop mot en gateway som redan sagt nej.
    """
    try:
        detail = "%s" % (exception,)
        response = getattr(exception, "response", None)
        if response is not None and not getattr(response, "is_closed", True):
            detail += " " + (response.text or "")
    except Exception:
        return False
    low = detail.lower()
    return "stream_options" in low or "include_usage" in low


# Kända strukturerade avslags-typer från gatewayen. Okänt format faller
# tillbaka på generiskt fel (fail-safe) — vi gissar inte på strängar.
#: Kända `type`-värden i gatewayens svarskropp.
#:
#: De tre 402-typerna lades till 2026-11-05: `proposal.md` för
#: gateway-anropseffektivitet säger uttryckligen att "tre olika 402:or —
#: Payment Required, credits are depleted, hard limit reached — är idag
#: oskiljbara", och 5.4:s lösning (läs orsaken ur SVARSKROPPEN) förutsätter
#: att typerna känns igen här. Utan dem föll alla 402 tillbaka på det
#: generiska felet, och de tre gick fortfarande inte att skilja åt — exakt
#: det problem ändringen skulle lösa.
_GATEWAY_REJECTION_TYPES = frozenset([
    'combo_forbidden', 'model_not_found', 'not_found',
    'entitlement_denied', 'forbidden',
    # 402 — tre skilda orsaker, samma HTTP-status
    'payment_required', 'credits_are_depleted', 'hard_limit_reached',
])


def map_gateway_rejection(status_code: int, body_text: str,
                          model: str) -> Optional[GatewayRejection]:
    """Mappa ett gateway-svar till ett strukturerat avslagsfel.

    Returnerar None om svaret inte är ett känt strukturerat avslag — då
    ska anroparen falla tillbaka på sitt generiska fel. Vi mappar på
    HTTP-status + känt `type`-fält, aldrig på fri text.
    """
    if not body_text:
        return None
    payload = _safe_json_loads(body_text, default=None)
    if not isinstance(payload, dict):
        return None
    # Gatewayen lägger `type` på TOPPNIVÅ och `message` inuti `error`:
    #   {"type": "payment_required", "error": {"message": "..."}}
    # Den tidigare koden tog `err = payload['error']` och letade `type` där —
    # så snart båda fälten fanns tappades typen, och de tre 402-typerna föll
    # tillbaka på det generiska felet. Mätt 2026-11-05: alla tre gav None.
    # Vi letar därför i BÅDA leden, toppnivå först.
    err = payload.get('error')
    if not isinstance(err, dict):
        err = payload
    reason = (payload.get('type') or payload.get('code')
              or err.get('type') or err.get('code') or '')
    message = err.get('message') or payload.get('message') or ''
    if reason in _GATEWAY_REJECTION_TYPES:
        return GatewayRejection(model, status_code, reason, message)
    # 403/404 med ett känt meddelande men okänt type-fält.
    if status_code in (403, 404) and message:
        low = message.lower()
        if 'entitle' in low or 'forbidden' in low:
            return GatewayRejection(model, status_code,
                                    'combo_forbidden', message)
        if 'not found' in low or 'unknown model' in low:
            return GatewayRejection(model, status_code,
                                    'model_not_found', message)
    return None


# ---------------------------------------------------------------------------
# Transient-fel-klassificering (provider-transient-retry D1)
# ---------------------------------------------------------------------------

#: Tak per väntan mellan två försök (provider-transient-retry D2). En
#: gateway som ber oss vänta längre än så kommer ändå inte att svara inom
#: rimlig tid för en interaktiv tur — då är ett tydligt fel bättre.
MAX_RETRY_WAIT = 8.0

#: Tak för antalet försök. `ai.provider.retry_count` klipps till 1..6:
#: 1 = ingen retry, 6 = värsta fall ~48 s väntan totalt.
MAX_RETRY_ATTEMPTS = 6

#: Kända övergående orsaker i en HTTP 400. En 400 är annars TERMINAL —
#: att retrya alla 400:or dubblar lasten vid en riktig bugg (se
#: `_iter_stream_retry`-docstringen, 2026-10-08). Matchningen sker mot
#: gatewayens strukturerade orsaksfält, aldrig mot fri meddelandetext.
_TRANSIENT_400_REASONS = frozenset([
    'failed to get config for provider',
    'no healthy upstream',
    'temporarily unavailable',
])

#: Event-typer som räknas som "innehåll har levererats till klienten".
#: Efter någon av dessa är en retry förbjuden — ett dubblerat svar eller
#: ett dubbelt verktygsanrop är värre än ett tydligt fel (D3).
_EMITTING_EVENT_TYPES = frozenset([
    'token', 'thinking', 'tool_call_start', 'tool_call_delta',
    'tool_call_end',
])


def _mark_emitted(chunk, state) -> None:
    """Sätt `state['emitted']` när en chunk bär levererat innehåll.

    Läser både råa OpenAI-chunks (dict) och `TokenEvent`. En `done` räknas
    inte som leverans — den avslutar bara strömmen.
    """
    if state is None:
        return
    try:
        if isinstance(chunk, TokenEvent):
            if chunk.type in _EMITTING_EVENT_TYPES and (
                    chunk.token or chunk.tool_call is not None):
                state['emitted'] = True
            return
        if isinstance(chunk, dict):
            choice = (chunk.get('choices') or [{}])[0]
            delta = choice.get('delta') or {}
            if (delta.get('content') or delta.get('tool_calls')
                    or delta.get('reasoning_content')
                    or delta.get('reasoning')):
                state['emitted'] = True
    except Exception:
        pass  # best-effort: tyst fallback — se etiketteringsregeln

#: Strukturerade orsakstyper som alltid är transienta, oavsett status.
_TRANSIENT_REASON_TYPES = frozenset([
    'rate_limit', 'rate_limited', 'policy_rate_limited',
    'overloaded', 'server_error', 'upstream_error',
    'temporarily_unavailable', 'service_unavailable',
])

#: Transienta HTTP-statusar (utöver 400 med känd orsak).
_TRANSIENT_STATUSES = frozenset([429, 500, 502, 503, 504])


def _parse_retry_after(value) -> Optional[float]:
    """Läs `Retry-After` (sekunder eller HTTP-datum) som sekunder.

    Returnerar None när värdet saknas eller inte går att tolka — då faller
    anroparen tillbaka på exponentiell backoff. Ett negativt värde (datum i
    det förflutna) klipps till 0.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return max(0.0, float(text))
    except (TypeError, ValueError):
        pass  # fallback: inte ett antal sekunder — prova HTTP-datum nedan
    try:
        when = parsedate_to_datetime(text)
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        delta = (when - datetime.now(timezone.utc)).total_seconds()
        return max(0.0, delta)
    except Exception:
        return None


@dataclass
class TransientVerdict:
    """Utfallet av en klassificering av ett provider-fel.

    is_transient — värt att försöka igen?
    reason       — kort orsaksetikett (loggas, visas för användaren)
    retry_after  — gatewayens egen väntetid i sekunder, om angiven
    """
    is_transient: bool = False
    reason: str = ''
    retry_after: Optional[float] = None


def _rejection_fields(body_text: str) -> tuple[str, str]:
    """Läs `(reason, message)` ur gatewayens strukturerade felsvar.

    Samma form som `map_gateway_rejection()` läser: `error.type` /
    `error.code`, med `extra_fields.error_type` som berikning. Returnerar
    tomma strängar när kroppen inte är JSON eller saknar fälten.
    """
    if not body_text:
        return '', ''
    payload = _safe_json_loads(body_text, default=None)
    if not isinstance(payload, dict):
        return '', ''
    err = payload.get('error')
    if not isinstance(err, dict):
        err = payload
    reason = err.get('type') or err.get('code') or ''
    message = err.get('message') or ''
    extra = payload.get('extra_fields')
    if isinstance(extra, dict) and extra.get('error_type'):
        reason = reason or str(extra['error_type'])
    # Toppnivåns `type`/`code` bär ibland orsaken när `error` är en sträng.
    if not reason:
        reason = payload.get('type') or payload.get('code') or ''
    return str(reason or ''), str(message or '')


def classify_transient(status_code: int, body_text: str = '',
                       headers=None) -> TransientVerdict:
    """Klassificera ett provider-svar som transient eller terminalt.

    Reglerna (provider-transient-retry):
      * 429, 500, 502, 503, 504            → transient
      * 400 med känd övergående orsak      → transient
      * 400 utan känd orsak                → terminalt
      * 401, 402, 403, 404                  → terminalt (avslag)
      * allt annat                          → terminalt

    Orsaken läses ur gatewayens strukturerade fält (`error.type`,
    `error.code`, `extra_fields.error_type`, `message`). Meddelandet
    konsulteras bara för de kända transienta 400-fraserna — aldrig som en
    fri textsökning över hela kroppen.
    """
    try:
        code = int(status_code or 0)
    except (TypeError, ValueError):
        code = 0  # fallback: icke-numerisk status är inte en HTTP-status

    retry_after = None
    if headers is not None:
        try:
            retry_after = _parse_retry_after(headers.get('Retry-After'))
        except Exception:
            retry_after = None

    reason_type, message = _rejection_fields(body_text)
    reason_low = reason_type.lower()
    message_low = message.lower()

    if code in _TRANSIENT_STATUSES:
        if code == 429:
            return TransientVerdict(True, 'rate_limit', retry_after)
        return TransientVerdict(
            True, reason_low or 'server_error', retry_after)

    if code == 400:
        if reason_low in _TRANSIENT_REASON_TYPES:
            return TransientVerdict(True, reason_low, retry_after)
        for known in _TRANSIENT_400_REASONS:
            if known in message_low or known in reason_low:
                return TransientVerdict(True, known, retry_after)
        return TransientVerdict(False, reason_low or 'bad_request', None)

    return TransientVerdict(False, reason_low or 'rejected', None)


# ---------------------------------------------------------------------------
# Retry logic (PROV-006)
# ---------------------------------------------------------------------------

def _is_retryable(exception: Exception) -> bool:
    """Är undantaget ett transportfel som får försökas om?

    Bara transportfel (connect/timeout/protokoll) räknas här. HTTP-
    statusfel lämnas med flit vidare till klassificeringen i
    `_classify_exception`, som läser svarskroppen — en statuskod ensam
    säger inte om felet är transient (400 är både och).

    Behålls som publik hjälpare: den uttrycker regeln "transportfel är
    retrybara" på ett ställe, och används av `_with_retry`-flödet via
    `_classify_exception`.
    """
    if isinstance(exception, GatewayRejection):
        return False
    if isinstance(exception, ProviderRetriesExhausted):
        return False
    if isinstance(exception, ProviderError):
        return exception.retryable
    if isinstance(exception, httpx.TransportError):
        return True
    return False


def _classify_exception(exception: Exception) -> TransientVerdict:
    """Klassificera ett undantag från ett provider-anrop.

    Bron mellan undantagsvärlden och `classify_transient()`: plockar ut
    statuskod, svarskropp och `Retry-After` ur ett `httpx`-fel.
    """
    if isinstance(exception, GatewayRejection):
        return TransientVerdict(False, exception.reason or 'rejected', None)
    if isinstance(exception, ProviderRetriesExhausted):
        return TransientVerdict(False, exception.last_reason or 'exhausted',
                                None)
    if isinstance(exception, httpx.HTTPStatusError):
        response = getattr(exception, 'response', None)
        status = getattr(response, 'status_code', 0) or 0
        body = ''
        headers = None
        try:
            if response is not None:
                headers = getattr(response, 'headers', None)
                body = response.text or ''
        except Exception:
            body = ''  # strömmen är stängd — se meddelandefallbacken nedan
        if not body:
            # `_stream_open`/`_post` bäddar in kroppen i meddelandet som
            # "... — provider: {json}". Läs den därifrån när response är stängd.
            text = str(exception)
            marker = 'provider: '
            idx = text.find(marker)
            if idx >= 0:
                body = text[idx + len(marker):]
        return classify_transient(status, body, headers)
    if isinstance(exception, httpx.TimeoutException):
        return TransientVerdict(True, 'timeout', None)
    if isinstance(exception, httpx.TransportError):
        return TransientVerdict(True, 'connection', None)
    return TransientVerdict(False, 'error', None)


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
        retry_count: int = 3,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        if not self.api_key:
            _logger.warning("AIProvider: ingen API-nyckel på recordet.")

        self.is_bifrost = is_bifrost
        self.api_style = api_style or "openai"
        self.timeout = timeout
        # provider-transient-retry D4: `ai.provider.retry_count` blir verklig
        # konfiguration. Klipps till 1..6 — 0 skulle annars kunna tolkas som
        # "oändligt" av en naiv loop, och 20 försök på en rate limit är
        # sämre än 3. OBS: `0 or 3` är 3 i Python, så 0-hanteringen måste ske
        # explicit innan klippningen — annars blir "stäng av retry" = 3 försök.
        try:
            _raw = 3 if retry_count is None else int(retry_count)
        except (TypeError, ValueError):
            _raw = 3  # fallback: ett icke-numeriskt värde → default
        self.retry_count = max(1, min(MAX_RETRY_ATTEMPTS, _raw))
        self._client: Optional[httpx.AsyncClient] = None

    # -- Retry-hölje (provider-transient-retry D2/D3) --

    @staticmethod
    def _compute_wait(verdict: TransientVerdict, attempt: int) -> float:
        """Väntetid inför försök nummer `attempt + 1`.

        Gatewayens `Retry-After` vinner när den finns (den vet bäst hur
        länge rate limit-fönstret är öppet). Annars exponentiellt växande
        med jitter — jitter hindrar att flera sessioner som fick 429 i
        samma sekund försöker igen i samma sekund.
        """
        if verdict is not None and verdict.retry_after is not None:
            return min(MAX_RETRY_WAIT, max(0.0, float(verdict.retry_after)))
        base = min(MAX_RETRY_WAIT, float(2 ** max(0, attempt)))
        return min(MAX_RETRY_WAIT, base * random.uniform(0.5, 1.5))

    async def _with_retry(self, factory, *, can_retry=None,
                          on_retry=None):
        """Kör `factory()` och försök igen vid transienta fel.

        `factory` är en coroutine-FABRIK (inte en coroutine): varje försök
        måste bygga en fräsch request, annars kan httpx inte skicka om den.

        `can_retry` är ett predikat som anropas före varje nytt försök. I
        streaming-vägen är det `lambda: not emitted` — har tokens redan nått
        klienten är ett dubblerat svar värre än ett fel.

        `on_retry(attempt, verdict)` anropas innan väntan, så att
        streaming-vägen kan skicka ett synligt "försöker igen"-event.

        Terminala fel och uttömda försök kastas vidare som
        `ProviderRetriesExhausted` respektive originalsfelet.
        """
        attempts_limit = self.retry_count
        attempt = 0
        last_verdict = TransientVerdict(False, 'error', None)
        last_exc = None
        while True:
            attempt += 1
            try:
                return await factory()
            except Exception as exc:  # noqa: BLE001 — klassificeras nedan
                verdict = _classify_exception(exc)
                last_exc, last_verdict = exc, verdict
                if not verdict.is_transient:
                    raise
                if attempt >= attempts_limit:
                    break
                if can_retry is not None and not can_retry():
                    _logger.warning(
                        'provider: transient fel (%s) men retry är förbjuden '
                        '— innehåll har redan sänts', verdict.reason)
                    raise
                wait = self._compute_wait(verdict, attempt - 1)
                _logger.warning(
                    'provider: %s (HTTP %s) — nytt försök %d/%d om %.1f s',
                    verdict.reason,
                    getattr(exc, 'response', None) is not None
                    and getattr(exc.response, 'status_code', '?') or '?',
                    attempt + 1, attempts_limit, wait)
                if on_retry is not None:
                    try:
                        on_retry(attempt, verdict)
                    except Exception:
                        _logger.debug('on_retry misslyckades', exc_info=True)
                if wait > 0:
                    await asyncio.sleep(wait)
                continue

        status = 0
        try:
            status = getattr(last_exc.response, 'status_code', 0) or 0
        except Exception:
            status = 0
        raise ProviderRetriesExhausted(
            'Providern gav upp efter %d försök — %s'
            % (attempt, last_verdict.reason or 'okänt fel'),
            status_code=status, attempts=attempt,
            last_reason=last_verdict.reason,
        ) from last_exc

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

    async def aclose(self):
        """Close the underlying httpx.AsyncClient (if created).

        Must be called before the owning asyncio event loop is closed —
        otherwise httpx leaves its internal connection/stream tasks pending,
        which triggers asyncio's ``Task was destroyed but it is pending!``
        warning (``async_generator_athrow``) and spams the Odoo log.
        """
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception:
                pass  # best-effort: tyst fallback — se etiketteringsregeln
            self._client = None

    async def chat(
        self,
        model: str,
        messages: list[Message],
        tools: Optional[list[dict]] = None,
        system_prompt: str = "",
        temperature: float = 0.7,
        max_tokens: int = 4096,
        extra_headers: Optional[dict] = None,
    ) -> ChatResponse:
        """Send a chat completion request. Non-streaming.

        extra_headers: per-anrops-headers (t.ex. Bifrosts `x-bf-session-id`).
        Klienten cachas per modell och kan betjäna flera sessioner, så
        sessionsidentiteten får INTE sättas vid klient-skapandet — den
        skickas här och läggs till ovanpå klientens bas-headers.
        """
        if not model:
            # bifrost-client-provisioning D6: ingen provider/modell konfigurerad
            # är en FELSITUATION — aldrig tyst hårdkodad fallback-sträng.
            raise ProviderError(
                "No model configured: set ai_provider_* in odoo.conf or "
                "assign a model to the agent (ai_agent_core.default_model_id)."
            )

        async def _attempt():
            if self.api_style == "anthropic":
                return await self._chat_anthropic(
                    model, messages, tools, system_prompt, temperature,
                    max_tokens, extra_headers=extra_headers,
                )
            return await self._chat_openai_compat(
                model, messages, tools, system_prompt, temperature,
                max_tokens, extra_headers=extra_headers,
            )

        # Retryn ligger HÄR och inte i de enskilda HTTP-hjälparna
        # (provider-transient-retry D2/D6): då täcks även de callsites som
        # ligger utanför AgentLoop (router, minnesextraktion, onboarding)
        # utan att var och en behöver bygga eget retry.
        return await self._with_retry(_attempt)

    async def chat_stream(
        self,
        model: str,
        messages: list[Message],
        tools: Optional[list[dict]] = None,
        system_prompt: str = "",
        temperature: float = 0.7,
        max_tokens: int = 4096,
        extra_headers: Optional[dict] = None,
    ) -> AsyncIterator[TokenEvent]:
        """Send a chat completion request. Streaming via async generator.

        extra_headers: se `chat()` — sätts per anrop, aldrig på klienten.
        """
        if not model:
            # D6: felsituation — ingen tyst hårdkodad fallback.
            raise ProviderError(
                "No model configured: set ai_provider_* in odoo.conf or "
                "assign a model to the agent (ai_agent_core.default_model_id)."
            )
        if self.api_style == "anthropic":
            async for event in self._stream_anthropic(
                model, messages, tools, system_prompt, temperature, max_tokens,
                extra_headers=extra_headers,
            ):
                yield event
        else:
            async for event in self._stream_openai_compat(
                model, messages, tools, system_prompt, temperature, max_tokens,
                extra_headers=extra_headers,
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
        include_usage: bool = False,
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
        if include_usage:
            # OpenAI-kompatibla providers rapporterar usage i sista chunk:en
            # när stream_options.include_usage sätts (krävs för korrekt
            # token-bokföring i web-chatten).
            body["stream_options"] = {"include_usage": True}
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

    async def _post(self, path: str, body: dict,
                    extra_headers: Optional[dict] = None) -> dict:
        """Ett HTTP-anrop. Retryn ligger i `_with_retry` (chat-nivå).

        `@DEFAULT_RETRY` togs bort härifrån (provider-transient-retry D2):
        den retryade bara transportfel och kunde inte se svarskroppen, så
        transienta 400:or och 429:or gick rakt igenom. Ligger retryn kvar
        på båda lagren multipliceras antalet anrop (3 × 3 = 9).
        """
        client = await self._get_client()
        url = f"{self.base_url}{path}"
        _logger.debug("AIProvider %s %s", path, body.get("model", "?"))
        response = await client.post(url, json=body, headers=extra_headers or None)
        if response.is_error:
            # Inkludera response-body i felet (t.ex. 400-detaljer från providern)
            _detail = response.text[:500]
            # Strukturerat gateway-avslag (403 combo_forbidden m.m.) mappas
            # till ett begripligt fel — annars ser användaren bara ett
            # generiskt anslutningsfel.
            rejection = map_gateway_rejection(
                response.status_code, _detail, body.get('model', '?'))
            if rejection:
                _logger.warning(
                    'Gateway avvisade modell %s: HTTP %s %s',
                    body.get('model', '?'), response.status_code,
                    rejection.reason)
                raise rejection
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as e:
                raise httpx.HTTPStatusError(
                    f"{e} — provider: {_detail}", request=e.request,
                    response=e.response) from e
        return response.json()

    async def _stream_open(self, url: str, body: dict,
                           extra_headers: Optional[dict] = None):
        """Öppna ett streaming-anrop (retryn ligger i `_with_retry`).

        Returnerar en öppen httpx-response redo att itereras. Vid statusfel
        stängs anslutningen innan raise, så nästa försök får en fräsch
        request.
        """
        client = await self._get_client()
        request = client.build_request('POST', url, json=body,
                                       headers=extra_headers or None)
        response = await client.send(request, stream=True)
        if response.is_error:
            await response.aread()  # läs body innan .text (streaming)
            _detail = response.text[:500]
            await response.aclose()
            # Strukturerat gateway-avslag — mappas innan den generiska vägen
            # (så streaming-vägen ger samma begripliga fel som _post).
            rejection = map_gateway_rejection(
                response.status_code, _detail, body.get('model', '?'))
            if rejection:
                _logger.warning(
                    'Gateway avvisade modell %s (stream): HTTP %s %s',
                    body.get('model', '?'), response.status_code,
                    rejection.reason)
                raise rejection
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

    async def _post_stream(self, path: str, body: dict,
                           extra_headers: Optional[dict] = None,
                           state: Optional[dict] = None):
        """Iterera en SSE-ström.

        `state` är en delad dict där `emitted` sätts när ett innehålls-
        `TokenEvent` yieldats. Den läses av `can_retry`-predikatet: har
        klienten redan fått tokens får anropet INTE göras om
        (provider-transient-retry D3).
        """
        client = await self._get_client()
        url = f"{self.base_url}{path}"
        body["stream"] = True
        _logger.debug("AIProvider stream %s %s", path, body.get("model", "?"))
        response = await self._stream_open(url, body, extra_headers=extra_headers)
        # httpx 0.28: Response saknar __aenter__ — stäng explicit i finally
        try:
            async for line in response.aiter_lines():
                if line.startswith("data: "):
                    data_str = line[6:]
                    if data_str == "[DONE]":
                        yield TokenEvent(type="done", finish_reason="stop")
                        return
                    try:
                        chunk = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue  # best-effort: tyst fallback — se etiketteringsregeln
                    if state is not None:
                        _mark_emitted(chunk, state)
                    yield chunk
        finally:
            await response.aclose()

    async def _iter_stream_retry(self, path: str, body: dict,
                                 extra_headers: Optional[dict] = None,
                                 state: Optional[dict] = None):
        """Iterera en SSE-ström med transient-retry — utan att buffra.

        Chunkarna yieldas VIDARE direkt när de kommer (realtidskravet i
        llm-streaming-retry). Ett `retry`-event yieldas i den strömmen precis
        innan väntan — klienten ser alltså väntan i realtid, inte först när
        nästa token kommer.

        Två retry-skäl, olika lager:

        1. **Transient fel** (429/5xx/transient 400/protokollfel) — försöks
           om med backoff så länge `state['emitted']` är falsk.

        2. **`stream_options` avvisas (400)** — ett strukturellt fel som
           kräver att bodyn ändras, inte att samma request görs om. Det
           hanteras här genom att ta bort `stream_options` och öppna igen,
           en gång, utan att räkna som ett transient försök.

        Historik (2026-10-08): retryen för 400 fick tidigare BARA ske när
        400:an faktiskt gällde stream_options. Tidigare räckte det att
        `stream_options` fanns i bodyn — och eftersom
        `_stream_openai_compat` alltid sätter include_usage=True var
        villkoret alltid sant. Varje 400 retryades då blint en gång, även
        när felet var något helt annat (t.ex. "Insufficient account funds").
        Det gav ett extra anrop, längre svarstid och att Bifrosts egen
        fallback-trappa kringgicks.
        """
        if state is None:
            state = {}
        attempt = 0
        limit = self.retry_count
        while True:
            attempt += 1
            try:
                async for chunk in self._post_stream(
                        path, body, extra_headers=extra_headers, state=state):
                    yield chunk
                return
            except Exception as exc:  # noqa: BLE001 — klassificeras nedan
                # Strukturellt fel: stream_options stöds inte. Ändra bodyn
                # och öppna igen — det är inte samma request, så det räknas
                # inte som ett transient försök.
                if (isinstance(exc, httpx.HTTPStatusError)
                        and getattr(exc, 'response', None) is not None
                        and exc.response.status_code == 400
                        and body.get("stream_options")
                        and _is_stream_options_rejection(exc)):
                    _logger.info(
                        'provider rejected stream_options (400); retrying '
                        'without include_usage: %s', body.get('model'))
                    body.pop("stream_options", None)
                    attempt -= 1  # inte ett transient försök
                    continue

                verdict = _classify_exception(exc)
                if not verdict.is_transient:
                    raise
                if state.get('emitted'):
                    _logger.warning(
                        'provider: transient fel (%s) efter levererade '
                        'tokens — retry förbjuden, innehåll är redan sänt',
                        verdict.reason)
                    raise
                if attempt >= limit:
                    status = 0
                    try:
                        if getattr(exc, 'response', None) is not None:
                            status = exc.response.status_code or 0
                    except Exception:
                        status = 0
                    raise ProviderRetriesExhausted(
                        'Providern gav upp efter %d försök — %s'
                        % (attempt, verdict.reason or 'okänt fel'),
                        status_code=status, attempts=attempt,
                        last_reason=verdict.reason) from exc

                wait = self._compute_wait(verdict, attempt - 1)
                _logger.warning(
                    'provider (stream): %s — nytt försök %d/%d om %.1f s',
                    verdict.reason, attempt + 1, limit, wait)
                # D5: synligt försök — yieldas FÖRE väntan, i realtid.
                yield TokenEvent(type="retry", attempt=attempt,
                                 reason=verdict.reason)
                if wait > 0:
                    await asyncio.sleep(wait)
                continue

    # -- OpenAI-compatible (OpenAI, DeepSeek, Cerebras, Groq, Google,
    #    OpenRouter, Ollama, custom) --

    async def _chat_openai_compat(self, model, messages, tools, system_prompt, temperature, max_tokens,
                                  extra_headers=None):
        body = self._build_openai_body(
            model, messages, tools, system_prompt, temperature, max_tokens
        )
        data = await self._post("/chat/completions", body, extra_headers=extra_headers)

        choice = data["choices"][0]
        msg = choice.get("message", {})

        tool_calls = []
        if msg.get("tool_calls"):
            for tc in msg["tool_calls"]:
                tool_calls.append(ToolCall(
                    id=tc["id"],
                    name=tc["function"]["name"],
                    arguments=_safe_json_loads(tc["function"]["arguments"]),
                ))

        return ChatResponse(
            text=msg.get("content", "") or "",
            tool_calls=tool_calls,
            input_tokens=data.get("usage", {}).get("prompt_tokens", 0),
            output_tokens=data.get("usage", {}).get("completion_tokens", 0),
            model=data.get("model", model),
            finish_reason=choice.get("finish_reason", "stop"),
        )

    async def _stream_openai_compat(self, model, messages, tools, system_prompt, temperature, max_tokens,
                                    extra_headers=None):
        body = self._build_openai_body(
            model, messages, tools, system_prompt, temperature, max_tokens,
            include_usage=True,
        )
        body["stream"] = True

        tool_call_buffer: dict[str, dict] = {}
        done_sent = False  # [DONE]-markören får inte ge en andra done
        last_usage: dict = {}

        async for chunk in self._iter_stream_retry("/chat/completions", body,
                                                   extra_headers=extra_headers):
            if isinstance(chunk, TokenEvent):
                if chunk.type == "done":
                    # Flush eventuella tool_call-buffers innan done
                    for idx, buf in tool_call_buffer.items():
                        try:
                            buf["arguments"] = _safe_json_loads(buf["arguments"])
                        except Exception:
                            buf["arguments"] = {}
                        yield TokenEvent(type="tool_call_end", tool_call=ToolCall(
                            id=buf["id"],
                            name=buf["name"],
                            arguments=buf["arguments"] if isinstance(buf["arguments"], dict) else {},
                        ))
                    tool_call_buffer.clear()
                    if done_sent:
                        continue
                    done_sent = True
                    # [DONE]-fallback (provider utan finish_reason-chunk):
                    # fäst ändå den usage vi sett på vägen.
                    if not getattr(chunk, 'input_tokens', 0) and last_usage:
                        chunk.input_tokens = (
                            last_usage.get("prompt_tokens")
                            or last_usage.get("input_tokens") or 0)
                        chunk.output_tokens = (
                            last_usage.get("completion_tokens")
                            or last_usage.get("output_tokens") or 0)
                yield chunk
                continue

            # Raw OpenAI chunk — fånga usage (include_usage ger en sista
            # chunk med usage men tomma choices; Bifrost rapporterar även
            # input_tokens/output_tokens i vissa svar).
            if chunk.get("usage"):
                last_usage = chunk["usage"]
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
                        buf["arguments"] = _safe_json_loads(buf["arguments"])
                    except Exception:
                        buf["arguments"] = {}
                    yield TokenEvent(type="tool_call_end", tool_call=ToolCall(
                        id=buf["id"],
                        name=buf["name"],
                        arguments=buf["arguments"] if isinstance(buf["arguments"], dict) else {},
                    ))
                tool_call_buffer.clear()
                if not done_sent:
                    done_sent = True
                    yield TokenEvent(
                        type="done", finish_reason=finish,
                        input_tokens=(
                            last_usage.get("prompt_tokens")
                            or last_usage.get("input_tokens") or 0),
                        output_tokens=(
                            last_usage.get("completion_tokens")
                            or last_usage.get("output_tokens") or 0),
                    )

    # -- Anthropic-specific (api_style=anthropic) --

    async def _chat_anthropic(self, model, messages, tools, system_prompt, temperature, max_tokens,
                              extra_headers=None):
        body = self._build_anthropic_body(model, messages, tools, system_prompt, temperature, max_tokens)
        data = await self._post("/messages", body, extra_headers=extra_headers)

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
            finish_reason=_normalize_finish_reason(data.get("stop_reason", "end_turn")),
        )

    async def _stream_anthropic(self, model, messages, tools, system_prompt, temperature, max_tokens,
                                extra_headers=None):
        body = self._build_anthropic_body(model, messages, tools, system_prompt, temperature, max_tokens)
        body["stream"] = True

        current_tool: dict | None = None
        # Anthropic rapporterar input_tokens i message_start och (kumulativa)
        # output_tokens i message_delta — fångas för korrekt token-bokföring.
        usage_input = 0
        usage_output = 0
        # provider-transient-retry D3: retry är bara tillåten innan något
        # innehåll sänts. `state['emitted']` sätts vid första levererade
        # eventet och läses av retry-loopen nedanför.
        state: dict = {}
        attempt = 0
        limit = self.retry_count
        while True:
            attempt += 1
            try:
                response = await self._stream_open(
                    f"{self.base_url}/messages", body,
                    extra_headers=extra_headers)
            except Exception as exc:  # noqa: BLE001 — klassificeras nedan
                verdict = _classify_exception(exc)
                if not verdict.is_transient or state.get('emitted'):
                    raise
                if attempt >= limit:
                    raise ProviderRetriesExhausted(
                        'Providern gav upp efter %d försök — %s'
                        % (attempt, verdict.reason or 'okänt fel'),
                        status_code=getattr(
                            getattr(exc, 'response', None), 'status_code', 0) or 0,
                        attempts=attempt,
                        last_reason=verdict.reason) from exc
                wait = self._compute_wait(verdict, attempt - 1)
                _logger.warning(
                    'provider (anthropic stream): %s — nytt försök %d/%d om %.1f s',
                    verdict.reason, attempt + 1, limit, wait)
                yield TokenEvent(type="retry", attempt=attempt,
                                 reason=verdict.reason)
                if wait > 0:
                    await asyncio.sleep(wait)
                continue
            break
        try:
            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue
                try:
                    event = json.loads(line[6:])
                except json.JSONDecodeError:
                    continue  # best-effort: tyst fallback — se etiketteringsregeln

                ev_type = event.get("type", "")
                if ev_type == "message_start":
                    usage_input = (
                        event.get("message", {}).get("usage", {}).get("input_tokens", 0)
                        or 0)
                elif ev_type == "message_delta":
                    usage_output = (
                        event.get("usage", {}).get("output_tokens", 0)
                        or 0)
                elif ev_type == "content_block_delta":
                    delta = event.get("delta", {})
                    if delta.get("type") == "text_delta":
                        state['emitted'] = True
                        yield TokenEvent(type="token", token=delta["text"])
                    elif delta.get("type") == "input_json_delta":
                        if current_tool:
                            current_tool["arguments"] = (
                                current_tool.get("arguments", "") + delta["partial_json"]
                            )
                elif ev_type == "content_block_start":
                    block = event.get("content_block", {})
                    if block.get("type") == "tool_use":
                        state['emitted'] = True
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
                            args = _safe_json_loads(current_tool["arguments"])
                        except Exception:
                            args = {}
                        yield TokenEvent(type="tool_call_end", tool_call=ToolCall(
                            id=current_tool["id"],
                            name=current_tool["name"],
                            arguments=args,
                        ))
                        current_tool = None
                elif ev_type == "message_stop":
                    yield TokenEvent(
                        type="done", finish_reason=_normalize_finish_reason("end_turn"),
                        input_tokens=usage_input, output_tokens=usage_output)
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

    # Bifrost: providerns api_key är den virtuella nyckeln (VK) och skickas som
    # X-Virtual-Key. Saknas den faller vi tillbaka på admin-nyckeln — SAMMA
    # regel som ai.provider.fetch_models() redan tillämpar. Utan denna fallback
    # skickades ingen X-Virtual-Key alls och Bifrost svarade 403
    # "combo is not entitled for this virtual key" — ett fel som ser ut som en
    # rättighetsfråga men i själva verket är en tom sträng.
    api_key = provider.api_key or ''
    if not api_key and getattr(provider, 'is_bifrost', False):
        api_key = (
            provider.env['ir.config_parameter'].sudo().get_param(
                'bifrost.admin_api_key', ''
            )
            or ''
        )
        if api_key:
            _logger.debug(
                "Bifrost-provider %s saknar api_key — använder "
                "bifrost.admin_api_key",
                provider.name,
            )

    return AIProvider(
        base_url=provider.base_url or '',
        api_key=api_key,
        is_bifrost=bool(getattr(provider, 'is_bifrost', False)),
        api_style=provider.api_style or 'openai',
        timeout=provider.timeout or 120.0,
        # provider-transient-retry D4: fältet fanns på recordet men lästes
        # aldrig — retry-antalet var hårdkodat 3 i dekoratorn.
        retry_count=getattr(provider, 'retry_count', 3) or 3,
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


def get_default_provider(env=None):
    """Default-providern när ingen medarbetare ger en.

    Kedja (default-coworker-som-provider D6):
      1. bolagets default-medarbetare → ledande agent → ai.model
      2. ir.config_parameter 'ai_agent_core.default_model_id' (Salt/reparation)
      3. (None, None)

    SIGNATUREN ÄR OFÖRÄNDRAD med flit: ~30 anropsplatser, varav flera i
    core/ (router.py, route.py, conference.py, tools.py) utan env eller
    coworker. Att byta signaturen skulle kräva en fyra raders kedja på
    varje sådant ställe. Att byta den INTERNA källan ger samma beteende
    till noll anropsplatsändringar.

    ⚠️ Namnet är nu något missvisande: resultatet kan tillhöra
    default-MEDARBETARENS agent, inte en lös parameter. Det är avsiktligt —
    funktionen svarar på "vad är default när ingen medarbetare anges".

    OBS (agent-model-resolution D3): den här funktionen krävde tidigare en
    HTTP-request (`odoo.http.request`) och returnerade därför ALLTID
    (None, None) i cron och shell — trots att cron är den vanligaste
    vägen in. Nu tas `env` emot, och `request` används bara som sista
    utväg.

    Args:
        env: Odoo environment. Om None, försök med `odoo.http.request`.
    """
    try:
        if env is None:
            from odoo.http import request
            if not request:
                return None, None
            env = request.env

        # 1. Bolagets default-medarbetare. Enda uppslagningen är
        #    res.company._ai_default_coworker() — den returnerar None när
        #    valet saknas eller är avaktiverat.
        try:
            default_coworker = env.company.sudo()._ai_default_coworker()
        except Exception:
            default_coworker = None
        if default_coworker:
            provider, model = resolve_provider_from_coworker(default_coworker)
            if provider:
                _logger.debug(
                    'get_default_provider: källa=coworker=%s',
                    default_coworker.id)
                return provider, model

        # 2. Den interna parametern (Salt: ai_default_model).
        param = env['ir.config_parameter'].sudo().get_param(
            'ai_agent_core.default_model_id'
        )
        if not param:
            _logger.debug('get_default_provider: källa=none')
            return None, None

        model = env['ai.model'].sudo().browse(int(param))
        if not model.exists():
            return None, None

        _logger.debug('get_default_provider: källa=param')
        return resolve_provider_from_model(model), model
    except Exception:
        return None, None


def get_default_model_name(env=None):
    """Namnet på default-modellen — ALLTID en sträng.

    Används som fallback när en agent saknar modell — aldrig hårdkodade
    leverantörsnamn. Löser via get_default_provider() (default-medarbetarens
    agent → parametern) och returnerar `api_name or name`, eller '' när
    ingen modell finns.

    KONTRAKT: returnerar en sträng, aldrig en coworker eller ett record.
    Det är kontraktet mot `provider.chat(model=...)` och mot de 22
    anropsplatserna.

    ⚠️ `env` är en BAKÅTKOOMPATIBEL tilläggsparameter (default None). Utan
    den faller funktionen tillbaka på `odoo.http.request` — och returnerade
    därför alltid '' i cron, shell och tester, där ingen request finns,
    trots att en env var tillgänglig på anropsplatsen. Samma fälla som
    `get_default_provider()` hade (agent-model-resolution D3).

    Args:
        env: Odoo environment. Om None, försök med `odoo.http.request`.
    """
    _provider, model = get_default_provider(env=env)
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


def resolve_coworker_or_default(coworker, env=None, raise_on_error=False):
    """(provider, ai.model) för coworkern — annars default-medarbetaren.

    Ersätter mönstret `provider_instance or get_default_provider()[0]`, som
    tyst tappade medarbetarens verktyg/skills när agenten saknade modell:
    den föll till en MODELLÖS parameter-provider, och körningen fortsatte
    utan den kontext användaren valt.

    Kedja (default-coworker-som-provider D3.1):
      1. coworkerns egen agent-kedja (ProviderFactory.from_coworker)
      2. bolagets default-medarbetare — samma kedja
      3. get_default_provider(env) (parametern/Salt) — sista utväg
      4. (None, None)

    Kastar normalt ALDRIG. Anroparens befintliga felväg (`raise ValueError` /
    `ValidationError`) ska ligga kvar oförändrad — helpern returnerar
    (None, None) och låter anroparen bestämma vad användaren ser.

    Undantag: `raise_on_error=True` används av obevakade körningar (cron),
    där en saknad provider är fatal och den underliggande orsaken måste
    bevaras. Utan den blir `ModuleNotFoundError: No module named 'tenacity'`
    till en intetsägande "ingen provider" — och operatören får gissa.

    Args:
        coworker: ai.coworker-record (får vara tom)
        env: Odoo environment. Behövs för default-uppslagningen när
             coworkern saknas eller är tom.
        raise_on_error: när True kastas det ursprungliga felet vidare i
             stället för att sväljas.

    Returns:
        (AIProvider, ai.model) eller (None, None)
    """
    try:
        if coworker:
            provider, model = resolve_provider_from_coworker(coworker)
            if provider:
                return provider, model

        # Fallback: bolagets default-medarbetare. Bara när den skiljer sig
        # från coworkern — annars upprepar vi samma misslyckade uppslag.
        _env = env
        if _env is None and coworker:
            _env = coworker.env
        if _env is None:
            try:
                from odoo.http import request
                _env = request.env if request else None
            except Exception:
                _env = None

        default_coworker = None
        if _env is not None:
            try:
                default_coworker = _env.company.sudo()._ai_default_coworker()
            except Exception:
                default_coworker = None

        if default_coworker and (
                not coworker or default_coworker.id != coworker.id):
            provider, model = resolve_provider_from_coworker(default_coworker)
            if provider:
                _logger.warning(
                    'Provider-fallback: coworker %s saknade upplösbar '
                    'modell — använder default-medarbetaren %s (%s)',
                    coworker.id if coworker else '-',
                    default_coworker.id, default_coworker.name)
                return provider, model

        # Sista utväg: parametern (Salt/reparation). Behålls för
        # installationer som ännu inte har någon default-medarbetare.
        provider, model = get_default_provider(env=_env)
        if provider:
            return provider, model
    except Exception:
        _logger.warning(
            'resolve_coworker_or_default misslyckades', exc_info=True)
        if raise_on_error:
            raise

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


# ---------------------------------------------------------------------------
# Synkron körning av provider-coroutines (B7)
# ---------------------------------------------------------------------------

def _close_provider_sync(provider):
    """Stäng en provider från SYNKRON kod — utan att skapa en ny loop.

    `aclose()` är async. Anropas den från sync-kod måste den köras någonstans.
    Vi vill INTE skapa en ny event-loop bara för att stänga (det var hela
    problemet: `asyncio.run()` stänger loopen men lämnade httpx-klienten
    öppen). I stället stänger vi den underliggande httpx-klienten direkt.
    """
    if provider is None:
        return
    client = getattr(provider, '_client', None)
    if client is None:
        return
    try:
        # httpx.AsyncClient.aclose() är async — men själva stängningen av
        # transporten kan göras synkront via transportens close(). Vi
        # använder den privata men stabila vägen och faller tillbaka tyst.
        transport = getattr(client, '_transport', None)
        if transport is not None and hasattr(transport, 'close'):
            transport.close()
    except Exception:  # noqa: BLE001 — städning får aldrig kasta
        pass  # best-effort: tyst fallback — se etiketteringsregeln
    finally:
        try:
            provider._client = None
        except Exception:  # noqa: BLE001
            pass  # best-effort: tyst fallback — se etiketteringsregeln


def _run_provider(coro, provider=None):
    """Kör en provider-coroutine och stäng providern — alltid.

    B7 (ai-agent-core-fault-remediation): nio callsites körde
    `asyncio.run(loop.run(prompt))` utan att stänga providern. Varje sådan
    körning lämnade en pending httpx-connection-task efter sig, och Odoo-
    loggen fylldes av:

        Task was destroyed but it is pending!
        task: <Task pending name='...' coro=<<async_generator_athrow ...>>

    Bara `ai_coworker.py` (koncept-extraktionen) stängde korrekt, och den
    gjorde det genom att bygga en egen `_run_and_close()`-wrapper på varje
    ställe. Den här hjälparen är samma mönster på ett ställe.

    Args:
        coro: coroutine att köra (t.ex. ``loop.run(prompt)``).
        provider: providern som äger httpx-klienten. Får vara None.

    Returns:
        Resultatet av coroutinen.

    Raises:
        Samma undantag som coroutinen — providern stängs ändå (finally).
    """
    import asyncio

    async def _wrapped():
        try:
            return await coro
        finally:
            if provider is not None and hasattr(provider, 'aclose'):
                try:
                    await provider.aclose()
                except Exception:  # noqa: BLE001 — städning får ej kasta
                    _logger.debug(
                        '_run_provider: aclose misslyckades', exc_info=True)

    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None

    if running is not None:
        # Vi är redan i en loop (t.ex. inuti en async-väg). asyncio.run()
        # skulle kasta RuntimeError. Kör i en egen tråd med egen loop.
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, _wrapped()).result()

    return asyncio.run(_wrapped())
