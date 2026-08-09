# -*- coding: utf-8 -*-
"""Odoo-integrationstester för fix-provider-resolution.

Körs med: checkmodule -d <db> -m ai_agent_core -t
Täcker: provider-matrix över alla PROVIDER_TYPES (upplösning utan
TypeError), header-logik per is_bifrost/api_style, base_url-respekt
(custom/ollama/openrouter), bifrost-virtual_key, standard-datafiler
(tom api_key) samt source_provider-fältet på ai.model (utan logik).
"""

import asyncio
import json
import os

from odoo.tests.common import TransactionCase

from odoo.addons.ai_agent_core.models.ai_provider import PROVIDER_TYPES
from odoo.addons.ai_agent_core.core.provider import (
    AIProvider,
    TokenEvent,
    resolve_provider_from_model,
)


def _run(coro):
    """Kör en async-koroutin i synkront test."""
    return asyncio.run(coro)


class TestProviderMatrix(TransactionCase):
    """Varje provider_type löses upp till AIProvider utan TypeError."""

    def _make_provider_model(self, provider_type, base_url=None):
        from odoo.addons.ai_agent_core.models.ai_provider import AIProvider as _M
        flags = _M._flags_from_type(provider_type)
        provider = self.env['ai.provider'].create({
            'name': f'Test {provider_type}',
            'provider_type': provider_type,
            'base_url': base_url or f'https://{provider_type}.example.com/v1',
            'is_bifrost': flags['is_bifrost'],
            'api_style': flags['api_style'],
        })
        ai_model = self.env['ai.model'].create({
            'name': f'test-{provider_type}-model',
            'provider': provider.id,
        })
        return provider, ai_model

    def test_all_provider_types_resolve(self):
        """Alla PROVIDER_TYPES-värden → AIProvider utan TypeError."""
        for ptype, _label in PROVIDER_TYPES:
            with self.subTest(provider_type=ptype):
                _provider, ai_model = self._make_provider_model(ptype)
                result = resolve_provider_from_model(ai_model)
                self.assertIsInstance(result, AIProvider)
                # Bifrost får X-Virtual-Key-flaggan
                from odoo.addons.ai_agent_core.models.ai_provider import AIProvider as _M
                flags = _M._flags_from_type(ptype)
                self.assertEqual(result.is_bifrost, flags['is_bifrost'])
                self.assertEqual(result.api_style, flags['api_style'])

    def test_unknown_type_still_resolves_from_record(self):
        """Okänd provider_type är inte ett problem — recordet bär allt."""
        provider = self.env['ai.provider'].create({
            'name': 'Okänd typ',
            'provider_type': 'custom',
            'base_url': 'https://okand.example.com/v1',
            'is_bifrost': False,
            'api_style': 'openai',
        })
        ai_model = self.env['ai.model'].create({
            'name': 'test-okand-model',
            'provider': provider.id,
        })
        result = resolve_provider_from_model(ai_model)
        self.assertIsInstance(result, AIProvider)
        self.assertEqual(result.base_url, 'https://okand.example.com/v1')

    def test_base_url_respected(self):
        """custom/ollama/openrouter får record:ets base_url — aldrig tyst api.openai.com."""
        cases = [
            ('custom', 'https://berget.ai/v1'),
            ('ollama', 'http://min-server:11434/v1'),
            ('openrouter', 'https://openrouter.ai/api/v1'),
        ]
        for ptype, url in cases:
            with self.subTest(provider_type=ptype, url=url):
                _provider, ai_model = self._make_provider_model(ptype, base_url=url)
                result = resolve_provider_from_model(ai_model)
                self.assertEqual(result.base_url, url.rstrip('/'))

    def test_trailing_slash_stripped(self):
        _provider, ai_model = self._make_provider_model(
            'openrouter', 'https://openrouter.ai/api/v1/')
        result = resolve_provider_from_model(ai_model)
        self.assertEqual(result.base_url, 'https://openrouter.ai/api/v1')

    def test_no_provider_returns_none(self):
        # provider är required=True — testa tom recordsets (finns inte)
        ai_model = self.env['ai.model'].search([('name', '=', 'finns-inte-modell')])
        self.assertIsNone(resolve_provider_from_model(ai_model))


class TestAuthHeaders(TransactionCase):
    """Header-logik: is_bifrost/api_style styr, ingen header utan nyckel."""

    async def _headers(self, **kw):
        provider = AIProvider(**kw)
        client = await provider._get_client()
        return client.headers

    def test_bifrost_x_virtual_key(self):
        headers = _run(self._headers(
            base_url='http://gw/v1', api_key='opencode', is_bifrost=True))
        self.assertEqual(headers.get('X-Virtual-Key'), 'opencode')
        self.assertNotIn('Authorization', headers)

    def test_bearer_only_with_key(self):
        headers = _run(self._headers(base_url='http://x/v1', api_key=''))
        self.assertNotIn('Authorization', headers)
        headers2 = _run(self._headers(base_url='http://x/v1', api_key='sk-test'))
        self.assertEqual(headers2.get('Authorization'), 'Bearer sk-test')

    def test_anthropic_headers(self):
        headers = _run(self._headers(
            base_url='https://api.anthropic.com/v1', api_key='sk-test',
            api_style='anthropic'))
        self.assertEqual(headers.get('x-api-key'), 'sk-test')
        self.assertIn('anthropic-version', headers)

class TestStreamingPattern(TransactionCase):
    """Streaming använder send(stream=True) + explicit aclose (httpx 0.28)."""

    class _FakeResponse:
        def __init__(self, lines):
            self._lines = list(lines)
            self.closed = False
            self.is_error = False
            self.status_code = 200
            self.text = ''

        async def aiter_lines(self):
            for line in self._lines:
                yield line

        async def aclose(self):
            self.closed = True

        def raise_for_status(self):
            pass

    class _FakeClient:
        def __init__(self, response):
            self._response = response
            self.sent = []

        def build_request(self, method, url, json=None):
            return {'method': method, 'url': url, 'json': json}

        async def send(self, request, stream=False):
            self.sent.append((request, stream))
            return self._response

    def test_post_stream_uses_send_stream_and_closes(self):
        async def _scenario():
            provider = AIProvider(base_url='http://test/v1')
            fake_resp = self._FakeResponse([
                'data: {"choices":[{"delta":{"content":"hej"}}]}',
                'data: [DONE]',
            ])
            fake_client = self._FakeClient(fake_resp)
            provider._client = fake_client
            events = []
            async for ev in provider._post_stream('/chat/completions', {'model': 'm'}):
                events.append(ev)
            return events, fake_client, fake_resp

        events, fake_client, fake_resp = _run(_scenario())
        # send anropades med stream=True
        self.assertTrue(fake_client.sent)
        _request, stream_flag = fake_client.sent[0]
        self.assertTrue(stream_flag)
        # explicit aclose skedde
        self.assertTrue(fake_resp.closed)
        # _post_stream yieldar dict-chunks + TokenEvent — filtrera
        token_events = [e for e in events if isinstance(e, TokenEvent)]
        # [DONE] → done-event
        self.assertTrue(any(e.type == 'done' for e in token_events))
        # data-raden 'hej' kom igenom som dict-chunk
        self.assertTrue(any(e.get('choices') for e in events if isinstance(e, dict)))

    def test_stream_openai_compat_emits_tokens(self):
        async def _scenario():
            provider = AIProvider(base_url='http://test/v1')
            fake_resp = self._FakeResponse([
                'data: {"choices":[{"delta":{"content":"A"}}]}',
                'data: {"choices":[{"delta":{"content":"B"},"finish_reason":"stop"}]}',
                'data: [DONE]',
            ])
            provider._client = self._FakeClient(fake_resp)
            from odoo.addons.ai_agent_core.core.provider import Message, Role
            events = []
            async for ev in provider._stream_openai_compat(
                    'm', [Message(role=Role.USER, content='hej')],
                    None, '', 0.7, 100):
                events.append(ev)
            return events

        events = _run(_scenario())
        tokens = [e.token for e in events if e.type == 'token']
        self.assertEqual(tokens, ['A', 'B'])
        dones = [e for e in events if e.type == 'done']
        self.assertEqual(len(dones), 1)


class TestSourceProviderField(TransactionCase):
    """source_provider är ett Many2one-fält på ai.model — inget logik än."""

    def test_field_exists_and_is_settable(self):
        flags_provider = self.env['ai.provider'].create({
            'name': 'Src Flags Provider',
            'provider_type': 'custom',
            'base_url': 'https://src.example.com/v1',
        })
        serving = self.env['ai.provider'].create({
            'name': 'Serving Provider',
            'provider_type': 'custom',
            'base_url': 'https://serve.example.com/v1',
        })
        ai_model = self.env['ai.model'].create({
            'name': 'test-src-model',
            'provider': serving.id,
            'source_provider': flags_provider.id,
        })
        self.assertEqual(ai_model.provider.id, serving.id)
        self.assertEqual(ai_model.source_provider.id, flags_provider.id)


class TestProviderDataFiles(TransactionCase):
    """Standard-datafiler: tom api_key, ingen api_key_env."""

    def test_data_files_exist_and_are_clean(self):
        import odoo.addons.ai_agent_core as mod_pkg
        data_dir = os.path.join(os.path.dirname(mod_pkg.__file__), 'data')
        expected = ['openai', 'anthropic', 'deepseek', 'google', 'cerebras',
                    'groq', 'ollama', 'openrouter', 'bifrost']
        for name in expected:
            path = os.path.join(data_dir, f'provider_{name}.xml')
            self.assertTrue(os.path.exists(path), f'saknas: {path}')
            content = open(path).read()
            self.assertNotIn('api_key_env', content,
                             f'{name}: api_key_env ska inte finnas (ingen pillar/env)')
            self.assertNotIn('<field name="api_key">', content,
                             f'{name}: api_key får inte finnas i datafil')

    def test_data_files_have_correct_flags(self):
        import odoo.addons.ai_agent_core as mod_pkg
        data_dir = os.path.join(os.path.dirname(mod_pkg.__file__), 'data')
        bifrost = open(os.path.join(data_dir, 'provider_bifrost.xml')).read()
        self.assertIn('is_bifrost" eval="True"', bifrost)
        anthropic = open(os.path.join(data_dir, 'provider_anthropic.xml')).read()
        self.assertIn('api_style">anthropic', anthropic)


class TestModelOffering(TransactionCase):
    """provider-model-offering: SKU-modell — api_name, source_provider-logik,
    UNIQUE (name, provider), display_name."""

    def _make_provider(self, name, ptype='custom', is_bifrost=False, **kw):
        return self.env['ai.provider'].create({
            'name': name,
            'provider_type': ptype,
            'base_url': f'https://{name.lower().replace(" ", "-")}.example.com/v1',
            'is_bifrost': is_bifrost,
            **kw,
        })

    def test_get_api_name_fallback(self):
        """Gammalt record utan api_name → name; med api_name → api_name."""
        provider = self._make_provider('Dir Provider')
        m = self.env['ai.model'].create({'name': 'deepseek-chat', 'provider': provider.id})
        self.assertEqual(m._get_api_name(), 'deepseek-chat')
        m.api_name = 'deepseek/deepseek-chat'
        self.assertEqual(m._get_api_name(), 'deepseek/deepseek-chat')

    def test_get_maker_gateway_vs_direct(self):
        """source_provider satt = tillverkare; tomt = provider."""
        bifrost = self._make_provider('Bifrost', ptype='bifrost', is_bifrost=True)
        deepseek = self._make_provider('DeepSeek', ptype='deepseek')
        m_gw = self.env['ai.model'].create({
            'name': 'deepseek-flash', 'api_name': 'deepseek/deepseek-flash',
            'provider': bifrost.id, 'source_provider': deepseek.id,
        })
        self.assertEqual(m_gw._get_maker(), deepseek)
        m_dir = self.env['ai.model'].create({
            'name': 'deepseek-chat', 'provider': deepseek.id,
        })
        self.assertEqual(m_dir._get_maker(), deepseek)

    def test_real_provider_derived_from_maker(self):
        bifrost = self._make_provider('Bifrost', ptype='bifrost', is_bifrost=True)
        deepseek = self._make_provider('DeepSeek', ptype='deepseek')
        m = self.env['ai.model'].create({
            'name': 'deepseek-flash', 'api_name': 'deepseek/deepseek-flash',
            'provider': bifrost.id, 'source_provider': deepseek.id,
        })
        self.assertEqual(m.real_provider, 'DeepSeek')
        m2 = self.env['ai.model'].create({
            'name': 'deepseek-chat', 'provider': deepseek.id,
        })
        self.assertEqual(m2.real_provider, 'DeepSeek')

    def test_unique_name_provider(self):
        provider = self._make_provider('Dir Provider')
        self.env['ai.model'].create({'name': 'deepseek-chat', 'provider': provider.id})
        with self.cr.savepoint():
            with self.assertRaises(Exception):
                self.env['ai.model'].create(
                    {'name': 'deepseek-chat', 'provider': provider.id})
        # samma namn, annan provider → tillåtet (olika SKU:er)
        provider2 = self._make_provider('Annan Provider')
        self.env['ai.model'].create({'name': 'deepseek-chat', 'provider': provider2.id})

    def test_display_name_format(self):
        provider = self._make_provider('Bifrost', ptype='bifrost', is_bifrost=True)
        m = self.env['ai.model'].create({'name': 'deepseek-flash', 'provider': provider.id})
        self.assertEqual(m.display_name, '[Bifrost] deepseek-flash')


class TestImportCanonicalization(TransactionCase):
    """Import från gateway: maker-upplösning, kanoniskt namn, source_provider."""

    def _gateway(self):
        return self.env['ai.provider'].create({
            'name': 'Bifrost', 'provider_type': 'bifrost',
            'base_url': 'http://gw:8080/v1', 'is_bifrost': True,
        })

    def _direct(self, name, ptype):
        return self.env['ai.provider'].create({
            'name': name, 'provider_type': ptype,
            'base_url': f'https://{name.lower()}.example.com/v1',
        })

    def test_nested_path_anthropic(self):
        """openrouter/anthropic/claude-sonnet-4 → maker Anthropic (inte OpenRouter)."""
        bifrost = self._gateway()
        self._direct('Anthropic', 'anthropic')
        bifrost._import_model('openrouter/anthropic/claude-sonnet-4')
        m = self.env['ai.model'].search([('provider', '=', bifrost.id)], limit=1)
        self.assertEqual(m.name, 'claude-sonnet-4')
        self.assertEqual(m.api_name, 'openrouter/anthropic/claude-sonnet-4')
        self.assertEqual(m.source_provider.name, 'Anthropic')

    def test_nested_path_google(self):
        """google/gemini/gemini-2.5-flash → maker Google (inte gemini)."""
        bifrost = self._gateway()
        self._direct('Google', 'google')
        bifrost._import_model('google/gemini/gemini-2.5-flash')
        m = self.env['ai.model'].search([('provider', '=', bifrost.id)], limit=1)
        self.assertEqual(m.name, 'gemini/gemini-2.5-flash')
        self.assertEqual(m.source_provider.name, 'Google')

    def test_direct_import_no_source(self):
        """Direkt provider: name = api_name, source_provider tom."""
        deepseek = self._direct('DeepSeek', 'deepseek')
        deepseek._import_model('deepseek-chat')
        m = self.env['ai.model'].search([('provider', '=', deepseek.id)], limit=1)
        self.assertEqual(m.name, 'deepseek-chat')
        self.assertEqual(m.api_name, 'deepseek-chat')
        self.assertFalse(m.source_provider)

    def test_import_idempotent(self):
        bifrost = self._gateway()
        self._direct('DeepSeek', 'deepseek')
        bifrost._import_model('deepseek/deepseek-flash')
        bifrost._import_model('deepseek/deepseek-flash')
        models = self.env['ai.model'].search([('provider', '=', bifrost.id)])
        self.assertEqual(len(models), 1)

    def test_unknown_maker_fallback_gateway(self):
        """Okänd tillverkare bakom gateway → find-or-create label-record."""
        bifrost = self._gateway()
        bifrost._import_model('oklab/ok-1')
        m = self.env['ai.model'].search([('provider', '=', bifrost.id)], limit=1)
        self.assertEqual(m.name, 'ok-1')
        self.assertEqual(m.api_name, 'oklab/ok-1')
        self.assertEqual(m.source_provider.name, 'Oklab')


class TestResolveFromReal(TransactionCase):
    """_resolve_from_real: kanal-medvetet — aldrig ilike+limit=1 på name."""

    def test_prefers_coworker_agent_models(self):
        """3 record delar kanoniskt name — coworkerns agent vinner."""
        bifrost = self.env['ai.provider'].create({
            'name': 'Bifrost', 'provider_type': 'bifrost', 'is_bifrost': True,
        })
        berget = self.env['ai.provider'].create({
            'name': 'Berget AI', 'provider_type': 'custom',
        })
        deepseek = self.env['ai.provider'].create({
            'name': 'DeepSeek', 'provider_type': 'deepseek',
        })
        m_bifrost = self.env['ai.model'].create({
            'name': 'deepseek-flash', 'api_name': 'deepseek/deepseek-flash',
            'provider': bifrost.id, 'source_provider': deepseek.id,
        })
        m_berget = self.env['ai.model'].create({
            'name': 'deepseek-flash', 'api_name': 'deepseek/deepseek-flash',
            'provider': berget.id, 'source_provider': deepseek.id,
        })
        agent = self.env['ai.agent'].create({
            'name': 'Ag', 'model_id': m_berget.id,
        })
        coworker = self.env['ai.coworker'].create({'name': 'Cw'})
        self.env['ai.coworker.agent'].create({
            'coworker_id': coworker.id, 'agent_id': agent.id,
        })
        resolved = self.env['ai.model']._resolve_from_real('deepseek-flash', coworker)
        self.assertEqual(resolved.id, m_berget.id)

    def test_exact_fallback_returns_one(self):
        """Utan coworker: exakt match på name/api_name — returnerar ett record."""
        p1 = self.env['ai.provider'].create({'name': 'P1', 'provider_type': 'custom'})
        p2 = self.env['ai.provider'].create({'name': 'P2', 'provider_type': 'custom'})
        p3 = self.env['ai.provider'].create({'name': 'P3', 'provider_type': 'custom'})
        m1 = self.env['ai.model'].create({'name': 'deepseek-flash', 'provider': p1.id})
        self.env['ai.model'].create({'name': 'deepseek-flash', 'provider': p2.id})
        m3 = self.env['ai.model'].create({
            'name': 'deepseek-flash', 'api_name': 'deepseek/deepseek-flash',
            'provider': p3.id,
        })
        resolved = self.env['ai.model']._resolve_from_real('deepseek-flash')
        self.assertIn(resolved.id, [m1.id, m3.id])
        # api_name-match fungerar också
        resolved2 = self.env['ai.model']._resolve_from_real('deepseek/deepseek-flash')
        self.assertEqual(resolved2.id, m3.id)
