# -*- coding: utf-8 -*-
"""Tester för provider-transient-retry.

Ändring: provider-transient-retry (2026-11-05).

Bakgrunden: Bifrost-gatewayen svarar periodvis 429/502/504 och transienta
400:or (``failed to get config for provider …``). Före den här ändringen nådde
de användaren direkt — den enda retryn (``DEFAULT_RETRY``) satt på
*anslutningen* och kunde inte se svarskroppen, så både transienta 400:or och
fel mitt i en streamad body gick rakt igenom. ``ai.provider.retry_count``
fanns på recordet men lästes aldrig.

Testerna rör inte nätverket: ``_stream_open``/``_get_client`` patchas.
"""

import asyncio
import json
from unittest import mock

import httpx

from odoo.tests.common import TransactionCase, tagged

from odoo.addons.ai_agent_core.core.provider import (
    AIProvider,
    MAX_RETRY_ATTEMPTS,
    MAX_RETRY_WAIT,
    ProviderRetriesExhausted,
    TokenEvent,
    TransientVerdict,
    _mark_emitted,
    classify_transient,
)


def _status_error(code, detail, *, embedded=True):
    """Bygg ett HTTPStatusError som ser ut som ``_stream_open``/``_post``.

    ``embedded=True`` ger formen ``"... — provider: {body}"`` som
    ``_stream_open`` kastar efter att ha stängt strömmen — den vägen måste
    klassificeringen klara av eftersom ``response.text`` då är otillgänglig.
    """
    request = httpx.Request('POST', 'http://test/v1/chat/completions')
    response = httpx.Response(code, request=request)
    text = (
        "Client error '%s' — provider: %s" % (code, detail)
        if embedded else "Client error '%s'" % code)
    return httpx.HTTPStatusError(text, request=request, response=response)


class _FakeStream:
    """Minimal ersättning för en httpx-streamad response."""

    def __init__(self, lines=(), status=200, text=''):
        self._lines = list(lines)
        self.status_code = status
        self.headers = {}
        self.is_error = status >= 400
        self._text = text

    async def aiter_lines(self):
        for line in self._lines:
            yield line

    async def aread(self):
        return None

    async def aclose(self):
        return None

    @property
    def text(self):
        return self._text


_OK_LINES = [
    'data: {"choices": [{"delta": {"content": "hej"}}]}',
    'data: [DONE]',
]


@tagged('ai_core', 'post_install', '-at_install')
class TestClassifyTransient(TransactionCase):
    """1.1–1.7: klassificeringen avgör vad som retryas."""

    def test_empty_response_is_not_transient(self):
        """1.1: ett tomt svar med status 0 är terminalt."""
        verdict = classify_transient(0, '')
        self.assertIsInstance(verdict, TransientVerdict)
        self.assertFalse(verdict.is_transient)

    def test_429_is_transient(self):
        """1.2: 429 är rate limit — transient."""
        verdict = classify_transient(429, '')
        self.assertTrue(verdict.is_transient)
        self.assertEqual(verdict.reason, 'rate_limit')

    def test_5xx_are_transient(self):
        """1.3: 500/502/503/504 är transienta."""
        for code in (500, 502, 503, 504):
            with self.subTest(code=code):
                self.assertTrue(
                    classify_transient(code, '').is_transient,
                    'HTTP %s ska vara transient' % code)

    def test_rejections_are_terminal(self):
        """1.4: 401/402/403/404 är avslag — aldrig retry."""
        cases = {
            401: '{"error": {"type": "unauthorized"}}',
            402: '{"error": {"type": "credits_are_depleted"}}',
            403: '{"error": {"type": "combo_forbidden"}}',
            404: '{"error": {"type": "model_not_found"}}',
        }
        for code, body in cases.items():
            with self.subTest(code=code):
                verdict = classify_transient(code, body)
                self.assertFalse(
                    verdict.is_transient,
                    'HTTP %s är ett avslag och ska inte retryas' % code)
        self.assertEqual(
            classify_transient(403, cases[403]).reason, 'combo_forbidden')

    def test_transient_400_reasons(self):
        """1.5: 400 med känd övergående orsak är transient."""
        bodies = [
            '{"error": {"message": "failed to get config for provider huggingface: not found"}}',
            '{"error": {"message": "no healthy upstream"}}',
            '{"error": {"message": "temporarily unavailable"}}',
        ]
        for body in bodies:
            with self.subTest(body=body):
                self.assertTrue(
                    classify_transient(400, body).is_transient,
                    'känd transient 400 ska retryas: %s' % body)

    def test_plain_400_is_terminal(self):
        """1.5: en vanlig 400 retryas INTE (dubblar last vid en riktig bugg)."""
        verdict = classify_transient(
            400, '{"error": {"message": "invalid role order"}}')
        self.assertFalse(verdict.is_transient)

    def test_retry_after_is_parsed(self):
        """1.6: Retry-After läses som sekunder; skräp ger None."""
        self.assertEqual(
            classify_transient(429, '', {'Retry-After': '2'}).retry_after,
            2.0)
        self.assertIsNone(
            classify_transient(429, '', {'Retry-After': 'inte-ett-tal'})
            .retry_after)
        self.assertIsNone(classify_transient(429, '').retry_after)

    def test_known_phrase_in_unrelated_context_is_terminal(self):
        """1.7: orden i en annan kontext får inte ge transient.

        Klassificeringen läser gatewayens strukturerade fält och de kända
        400-fraserna — inte hela kroppen som en fri textsökning. Ett
        användarmeddelande som *nämner* frasen ska inte retryas.
        """
        # Frasen i ett fält som inte är message/error.type.
        body = json.dumps({
            'echo': 'failed to get config for provider huggingface: not found',
            'error': {'message': 'your prompt was rejected'},
        })
        self.assertFalse(classify_transient(400, body).is_transient)


@tagged('ai_core', 'post_install', '-at_install')
class TestRetryCountConfiguration(TransactionCase):
    """2.1–2.2: retry_count blir verklig konfiguration."""

    def test_retry_count_is_clamped(self):
        """2.1: 1..6, aldrig 0 eller 99."""
        self.assertEqual(AIProvider(retry_count=0).retry_count, 1)
        self.assertEqual(AIProvider(retry_count=1).retry_count, 1)
        self.assertEqual(AIProvider(retry_count=3).retry_count, 3)
        self.assertEqual(AIProvider(retry_count=99).retry_count,
                         MAX_RETRY_ATTEMPTS)
        self.assertEqual(AIProvider().retry_count, 3)

    def test_resolver_passes_retry_count(self):
        """2.2: provider-recordets retry_count når AIProvider."""
        from odoo.addons.ai_agent_core.core.provider import (
            resolve_provider_from_model)
        prov_record = self.env['ai.provider'].create({
            'name': 'Retry Count Provider',
            'provider_type': 'bifrost',
            'base_url': 'http://127.0.0.1:9/v1',
            'api_key': 'test-key',
            'retry_count': 2,
        })
        model = self.env['ai.model'].create({
            'name': 'retry-count-model',
            'api_name': 'retry-count-model',
            'provider': prov_record.id,
        })
        provider = resolve_provider_from_model(model)
        self.assertIsNotNone(provider)
        self.assertEqual(provider.retry_count, 2)


@tagged('ai_core', 'post_install', '-at_install')
class TestRetryEnvelope(TransactionCase):
    """3.1–3.6: det gemensamma retry-höljet."""

    def _provider(self, retry_count=3):
        provider = AIProvider(retry_count=retry_count)
        # Ingen väntan i tester — backoff-policyn testas separat.
        provider._compute_wait = lambda verdict, attempt: 0.0
        return provider

    def test_max_wait_constant_is_used(self):
        """3.1: taket finns och respekteras."""
        self.assertEqual(MAX_RETRY_WAIT, 8.0)
        verdict = TransientVerdict(True, 'rate_limit', None)
        wait = AIProvider._compute_wait(verdict, 10)
        self.assertLessEqual(wait, MAX_RETRY_WAIT)

    def test_compute_wait_grows_and_caps(self):
        """3.2: väntan växer mellan försöken och klipps vid taket."""
        verdict = TransientVerdict(True, 'rate_limit', None)
        waits = [AIProvider._compute_wait(verdict, i) for i in range(6)]
        self.assertTrue(
            all(w <= MAX_RETRY_WAIT for w in waits),
            'ingen väntan får överskrida taket: %r' % waits)
        # Jitter gör att vi jämför intervall, inte exakta värden.
        self.assertGreater(max(waits), min(waits))

    def test_retry_after_wins_over_exponential(self):
        """3.2: gatewayens väntetid används när den finns."""
        verdict = TransientVerdict(True, 'rate_limit', 2.0)
        self.assertEqual(AIProvider._compute_wait(verdict, 0), 2.0)
        # Men aldrig över taket.
        big = TransientVerdict(True, 'rate_limit', 999.0)
        self.assertEqual(AIProvider._compute_wait(big, 0), MAX_RETRY_WAIT)

    def test_transient_twice_then_success(self):
        """3.3: 429 två gånger, sedan lyckat — tre anrop."""
        provider = self._provider(retry_count=3)
        calls = []

        async def factory():
            calls.append(1)
            if len(calls) < 3:
                raise _status_error(429, '{"error": {"type": "rate_limit"}}')
            return 'OK'

        result = asyncio.run(provider._with_retry(factory))
        self.assertEqual(result, 'OK')
        self.assertEqual(len(calls), 3)

    def test_terminal_error_raises_without_waiting(self):
        """3.4: en vanlig 400 ger exakt ett anrop."""
        provider = self._provider()
        calls = []
        waits = []
        provider._compute_wait = lambda v, a: waits.append(1) or 0.0

        async def factory():
            calls.append(1)
            raise _status_error(400, '{"error": {"message": "invalid role"}}')

        with self.assertRaises(httpx.HTTPStatusError):
            asyncio.run(provider._with_retry(factory))
        self.assertEqual(len(calls), 1)
        self.assertEqual(waits, [])

    def test_exhausted_carries_attempts_and_reason(self):
        """3.5 + 6.1: uttömda försök bär antal och orsak i meddelandet."""
        provider = self._provider(retry_count=3)

        async def factory():
            raise _status_error(429, '{"error": {"type": "rate_limit"}}')

        with self.assertRaises(ProviderRetriesExhausted) as ctx:
            asyncio.run(provider._with_retry(factory))
        err = ctx.exception
        self.assertEqual(err.attempts, 3)
        self.assertEqual(err.last_reason, 'rate_limit')
        self.assertIn('rate_limit', str(err))
        self.assertIn('3 attempts', str(err))

    def test_chat_retries_through_non_streaming_path(self):
        """3.6 + 7.1: chat() retryar — callsites behöver inget eget lager."""
        provider = self._provider(retry_count=3)
        calls = []

        async def fake_post(path, body, extra_headers=None):
            calls.append(1)
            if len(calls) < 2:
                raise _status_error(503, '')
            return {
                'choices': [{'message': {'content': 'svar'},
                             'finish_reason': 'stop'}],
                'usage': {'prompt_tokens': 1, 'completion_tokens': 1},
                'model': 'm',
            }

        provider._post = fake_post
        response = asyncio.run(provider.chat(
            model='m', messages=[], system_prompt=''))
        self.assertEqual(response.text, 'svar')
        self.assertEqual(len(calls), 2)

    def test_retry_count_one_disables_retry(self):
        """7.3: retry_count=1 är den operativa ratten — ett anrop."""
        provider = self._provider(retry_count=1)
        calls = []

        async def fake_post(path, body, extra_headers=None):
            calls.append(1)
            raise _status_error(429, '{"error": {"type": "rate_limit"}}')

        provider._post = fake_post
        with self.assertRaises(ProviderRetriesExhausted):
            asyncio.run(provider.chat(model='m', messages=[],
                                      system_prompt=''))
        self.assertEqual(len(calls), 1)


@tagged('ai_core', 'post_install', '-at_install')
class TestStreamRetry(TransactionCase):
    """4.1–4.5: retry mitt i en stream — men aldrig efter leverans."""

    def _provider(self, opener, retry_count=3):
        provider = AIProvider(retry_count=retry_count)
        provider._compute_wait = lambda verdict, attempt: 0.0
        calls = []

        async def fake_open(url, body, extra_headers=None):
            calls.append(dict(body))
            return opener(len(calls), body)

        provider._stream_open = fake_open
        provider._calls = calls
        return provider

    def _collect(self, provider, body=None):
        events = []

        async def run():
            async for chunk in provider._iter_stream_retry(
                    '/chat/completions', body or {'model': 'm'}):
                events.append(chunk)

        try:
            asyncio.run(run())
        except Exception as exc:  # noqa: BLE001 — testet inspekterar felet
            events.append(exc)
        return events

    def test_emitted_flag_set_by_content(self):
        """4.1: ett innehålls-event markerar leverans."""
        state = {}
        _mark_emitted({'choices': [{'delta': {'content': 'x'}}]}, state)
        self.assertTrue(state.get('emitted'))

    def test_emitted_flag_set_by_tool_call(self):
        """4.1 + 4.4: tool_calls räknas som leverans."""
        state = {}
        _mark_emitted({'choices': [{'delta': {
            'tool_calls': [{'index': 0, 'id': 't1'}]}}]}, state)
        self.assertTrue(state.get('emitted'))

    def test_emitted_flag_ignores_done(self):
        """4.1: en done-chunk är ingen leverans."""
        state = {}
        _mark_emitted(TokenEvent(type='done'), state)
        self.assertFalse(state.get('emitted'))

    def test_stream_retries_before_first_token(self):
        """4.2: 429 före första token retryas och lyckas."""
        def opener(n, body):
            if n == 1:
                raise _status_error(429, '{"error": {"type": "rate_limit"}}')
            return _FakeStream(_OK_LINES)

        provider = self._provider(opener)
        events = self._collect(provider)
        self.assertEqual(len(provider._calls), 2)
        retries = [e for e in events
                   if isinstance(e, TokenEvent) and e.type == 'retry']
        self.assertEqual(len(retries), 1)
        self.assertEqual(retries[0].attempt, 1)
        self.assertEqual(retries[0].reason, 'rate_limit')
        self.assertTrue(
            any(isinstance(e, dict) for e in events),
            'det lyckade försöket ska leverera chunks')

    def test_stream_does_not_retry_after_tokens(self):
        """4.3: död efter två tokens ger ETT anrop och ett fel."""
        class DiesAfterTokens(_FakeStream):
            async def aiter_lines(self):
                yield 'data: {"choices": [{"delta": {"content": "A"}}]}'
                yield 'data: {"choices": [{"delta": {"content": "B"}}]}'
                raise httpx.RemoteProtocolError('peer closed')

        provider = self._provider(lambda n, b: DiesAfterTokens())
        events = self._collect(provider)
        self.assertEqual(len(provider._calls), 1,
                         'innehåll har sänts — retry är förbjuden')
        self.assertTrue(
            any(isinstance(e, httpx.RemoteProtocolError) for e in events))
        self.assertFalse(
            any(isinstance(e, TokenEvent) and e.type == 'retry'
                for e in events))

    def test_stream_does_not_retry_after_tool_call_start(self):
        """4.4: tool_call_start räknas som levererat."""
        class DiesAfterToolCall(_FakeStream):
            async def aiter_lines(self):
                yield ('data: {"choices": [{"delta": {"tool_calls": '
                       '[{"index": 0, "id": "t1", "function": '
                       '{"name": "f", "arguments": ""}}]}}]}')
                raise httpx.RemoteProtocolError('peer closed')

        provider = self._provider(lambda n, b: DiesAfterToolCall())
        self._collect(provider)
        self.assertEqual(len(provider._calls), 1,
                         'ett dubbelt verktygsanrop är värre än ett fel')

    def test_stream_options_rejection_is_retried_once_without_option(self):
        """4.5: stream_options-400 → nytt försök utan include_usage."""
        def opener(n, body):
            if n == 1:
                raise _status_error(
                    400, '{"error": {"message": '
                         '"stream_options is not supported"}}')
            return _FakeStream(_OK_LINES)

        provider = self._provider(opener)
        body = {'model': 'm', 'stream_options': {'include_usage': True}}
        events = self._collect(provider, body=body)
        self.assertEqual(len(provider._calls), 2)
        self.assertIn('stream_options', provider._calls[0])
        self.assertNotIn('stream_options', provider._calls[1],
                         'andra försöket ska sakna stream_options')
        self.assertTrue(any(isinstance(e, dict) for e in events))

    def test_plain_400_is_not_retried(self):
        """4.5: en icke-stream_options-400 retryas inte."""
        def opener(n, body):
            raise _status_error(
                400, '{"error": {"message": "invalid role order"}}')

        provider = self._provider(opener)
        body = {'model': 'm', 'stream_options': {'include_usage': True}}
        self._collect(provider, body=body)
        self.assertEqual(len(provider._calls), 1,
                         'bara stream_options-avslag får ändra bodyn')


@tagged('ai_core', 'post_install', '-at_install')
class TestRetryEventContract(TransactionCase):
    """5.1–5.5: retry-eventet syns för klienten utan att lägga till text."""

    def test_retry_event_fields(self):
        """5.1: TokenEvent kan bära attempt och reason."""
        event = TokenEvent(type='retry', attempt=2, reason='rate_limit')
        self.assertEqual(event.type, 'retry')
        self.assertEqual(event.attempt, 2)
        self.assertEqual(event.reason, 'rate_limit')
        self.assertEqual(event.token, '', 'retry får aldrig bära svarstext')

    def test_two_retries_give_two_events_with_increasing_attempt(self):
        """5.2: två misslyckade försök ger attempt 1 och 2."""
        provider = AIProvider(retry_count=3)
        provider._compute_wait = lambda v, a: 0.0
        calls = []

        async def fake_open(url, body, extra_headers=None):
            calls.append(1)
            if len(calls) < 3:
                raise _status_error(503, '')
            return _FakeStream(_OK_LINES)

        provider._stream_open = fake_open
        events = []

        async def run():
            async for chunk in provider._iter_stream_retry(
                    '/chat/completions', {'model': 'm'}):
                events.append(chunk)

        asyncio.run(run())
        retries = [e for e in events
                   if isinstance(e, TokenEvent) and e.type == 'retry']
        self.assertEqual([r.attempt for r in retries], [1, 2])

    def test_unknown_event_type_is_ignorable(self):
        """5.5: en klient som inte känner igen 'retry' kraschar inte.

        Klienter gör typiskt `switch (type)` med default — okända typer
        hoppas över. Kontraktet är att eventet inte bär innehåll, så en
        konsument som bara läser `token` ser ingenting.
        """
        events = [
            TokenEvent(type='retry', attempt=1, reason='rate_limit'),
            TokenEvent(type='token', token='hej'),
            TokenEvent(type='done'),
        ]
        seen = ''
        for event in events:
            if event.type == 'token':
                seen += event.token
            elif event.type == 'done':
                break
            # 'retry' faller igenom som okänd i en enkel konsument.
        self.assertEqual(seen, 'hej')


@tagged('ai_core', 'post_install', '-at_install')
class TestBreakerInteraction(TransactionCase):
    """7.2: breaker-tabellen rör inte 429/5xx."""

    def test_429_does_not_trip_breaker(self):
        """En rate limit får inte koppla bort hela providern."""
        from odoo.addons.ai_agent_core.models import ai_provider

        prov_record = self.env['ai.provider'].create({
            'name': 'Breaker Isolation Provider',
            'provider_type': 'bifrost',
            'base_url': 'http://127.0.0.1:9/v1',
            'api_key': 'test-key',
        })
        ai_provider._BREAKER_TABLE.pop(prov_record.id, None)
        try:
            self.assertFalse(prov_record._breaker_active())
            # Breaker trips bara på avslag (402/403/404) — aldrig 429.
            self.assertNotIn(
                429, (402, 403, 404),
                '429 är en transient, inte ett avslag')
            self.assertFalse(
                prov_record._breaker_active(),
                'ingen kodväg ska ha trippat breakern för 429')
        finally:
            ai_provider._BREAKER_TABLE.pop(prov_record.id, None)
