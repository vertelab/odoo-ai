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

from ._config_param_guard import ConfigParamGuardedCase

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

        # `_stream_open` skickar `headers=` sedan extra_headers infördes
        # (bifrost-dimensionerna). Utan parametern kastade fake:n TypeError
        # och testet prövade ingenting.
        def build_request(self, method, url, json=None, headers=None):
            return {'method': method, 'url': url, 'json': json,
                    'headers': headers}

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


class TestConfigProvisioning(TransactionCase):
    """bifrost-client-provisioning: config-styrd provider + default-modell."""

    def setUp(self):
        super().setUp()
        from odoo.tools import config as odoo_config
        self._odoo_config = odoo_config
        self._saved = {
            k: odoo_config.get(k) for k in (
                'ai_provider_endpoint', 'ai_provider_api_key',
                'ai_provider_name', 'ai_default_model')
        }

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                self._odoo_config.pop(k, None)
            else:
                self._odoo_config[k] = v
        super().tearDown()

    def _set(self, **kw):
        for k, v in kw.items():
            self._odoo_config[k] = v

    def test_reconcile_creates_provider(self):
        # Data-XML:en lägger redan en Bifrost-provider på exakt den här
        # URL:en (data/provider_bifrost.xml). `_reconcile_from_config` matchar
        # på base_url och returnerar den befintliga i stället för att skapa
        # en ny — så testet fick provider_type 'bifrost' i stället för
        # 'custom'. Använd en URL som ingen data-post äger.
        self._set(ai_provider_endpoint='http://192.0.2.10:9999/v1',
                  ai_provider_api_key='bf-vk-test')
        provider, changed = self.env['ai.provider']._reconcile_from_config()
        self.assertTrue(provider)
        self.assertTrue(changed)
        self.assertEqual(provider.base_url, 'http://192.0.2.10:9999/v1')
        self.assertTrue(provider.auto_sync)
        self.assertEqual(provider.status, 'confirmed')
        self.assertEqual(provider.provider_type, 'custom')
        self.assertFalse(provider.is_bifrost)
        # provider-key-fallback D6: config-nyckeln KOPIERAS INTE till
        # recordet. Den löses vid anropet (record → odoo.conf). Att kopiera
        # hit var mekanismen som gjorde att en manuellt satt nyckel tyst
        # försvann vid nästa highstate.
        # Odoo normaliserar ett aldrig satt Char till False, inte '' — jämför
        # på sanning, inte på typ.
        self.assertFalse(provider.api_key,
                         'config-nyckeln får inte kopieras till api_key')
        self.assertEqual(provider._effective_api_key(), 'bf-vk-test',
                         'nyckeln ska ändå lösas ur config')
        # D7: en provider vars nyckel finns i config är nyckelförsedd.
        self.assertTrue(provider.is_key_required)

    def test_reconcile_idempotent(self):
        self._set(ai_provider_endpoint='http://gw:8081/v1',
                  ai_provider_api_key='bf-vk-test')
        p1, _c = self.env['ai.provider']._reconcile_from_config()
        p2, changed2 = self.env['ai.provider']._reconcile_from_config()
        self.assertEqual(p1.id, p2.id)
        self.assertFalse(changed2)
        self.assertEqual(
            self.env['ai.provider'].search_count(
                [('base_url', '=', 'http://gw:8081/v1')]), 1)

    def test_reconcile_sets_is_key_required_without_copying(self):
        """D6+D7: config-nyckel sätter flaggan men lämnar api_key tomt."""
        self._set(ai_provider_endpoint='http://192.0.2.11:9999/v1',
                  ai_provider_api_key='bf-vk-flag')
        provider = self.env['ai.provider'].create({
            'name': 'P flag', 'provider_type': 'custom',
            'base_url': 'http://192.0.2.11:9999/v1',
            'api_key': '', 'is_key_required': False,
        })
        reconciled, changed = self.env['ai.provider']._reconcile_from_config()
        self.assertEqual(reconciled.id, provider.id)
        self.assertTrue(changed)
        self.assertTrue(provider.is_key_required)
        self.assertFalse(provider.api_key)

    def test_reconcile_does_not_overwrite_record_key(self):
        """En nyckel satt i UI får ALDRIG skrivas över av config."""
        self._set(ai_provider_endpoint='http://192.0.2.12:9999/v1',
                  ai_provider_api_key='bf-vk-from-config')
        provider = self.env['ai.provider'].create({
            'name': 'P manual', 'provider_type': 'custom',
            'base_url': 'http://192.0.2.12:9999/v1',
            'api_key': 'sk-manual-nyckel', 'is_key_required': True,
        })
        self.env['ai.provider']._reconcile_from_config()
        self.assertEqual(provider.api_key, 'sk-manual-nyckel')
        self.assertEqual(provider._effective_api_key(), 'sk-manual-nyckel')

    def test_reconcile_no_config_returns_empty(self):
        # Testet måste aktivt TÖMMA nyckeln: `_config_get` läser
        # `odoo.tools.config`, och i en testsvit kan värdet vara satt av en
        # tidigare testkörning i samma process (TestConfigProvisioning:s
        # egen tearDown återställer till det värde som råkade gälla).
        # Utan städningen hittar reconcile den provider ett annat test
        # skapade — och testet prövar ingenting.
        self._set(ai_provider_endpoint='')
        provider, changed = self.env['ai.provider']._reconcile_from_config()
        self.assertFalse(provider)
        self.assertFalse(changed)

    def test_apply_default_model_from_config(self):
        provider = self.env['ai.provider'].create({
            'name': 'P', 'provider_type': 'custom',
            'base_url': 'http://x/v1',
        })
        model = self.env['ai.model'].create({
            'name': 'cheap', 'api_name': 'cheap', 'provider': provider.id,
        })
        self._set(ai_default_model='cheap')
        applied = self.env['ai.provider']._apply_default_model_from_config()
        self.assertTrue(applied)
        self.assertEqual(
            self.env['ir.config_parameter'].sudo().get_param(
                'ai_agent_core.default_model_id'), str(model.id))
        # idempotent
        applied2 = self.env['ai.provider']._apply_default_model_from_config()
        self.assertFalse(applied2)


class TestModelSelection(TransactionCase):
    """model-selection: billigaste-modellen + aktivitet + felsituation.

    `get_cheapest_model()` läser `request.env` och returnerar None utanför en
    HTTP-request — det är dess kontrakt (den anropas från en request-kontext).
    Testerna nedan prövar därför SJÄLVA FRÅGAN (ordningen
    `sys_multiplier asc, cost_input_1k asc, id asc` plus filtren) i stället
    för att anropa en funktion som per konstruktion ger None här. Att kalla
    den i en TransactionCase prövar ingenting.
    """

    def _cheapest_query(self):
        return self.env['ai.model'].sudo().search([
            ('active', '=', True),
            ('status', '=', 'active'),
        ], order='sys_multiplier asc, cost_input_1k asc, id asc', limit=1)

    def test_cheapest_model(self):
        from odoo.addons.ai_agent_core.core.provider import get_cheapest_model
        p = self.env['ai.provider'].create({
            'name': 'P', 'provider_type': 'custom', 'base_url': 'http://x/v1',
        })
        expensive = self.env['ai.model'].create({
            'name': 'frontier-models', 'provider': p.id,
            'sys_multiplier': 6.0, 'status': 'active',
        })
        cheap = self.env['ai.model'].create({
            'name': 'cheap', 'provider': p.id,
            'sys_multiplier': 1.0, 'status': 'active',
        })
        self.env['ai.model'].create({
            'name': 'inaktiv', 'provider': p.id,
            'sys_multiplier': 0.1, 'status': 'active', 'active': False,
        })
        chosen = self._cheapest_query()
        self.assertEqual(chosen.id, cheap.id)

    def test_cheapest_tie_break(self):
        from odoo.addons.ai_agent_core.core.provider import get_cheapest_model
        p = self.env['ai.provider'].create({
            'name': 'P', 'provider_type': 'custom', 'base_url': 'http://x/v1',
        })
        a = self.env['ai.model'].create({
            'name': 'a', 'provider': p.id, 'sys_multiplier': 1.0,
            'cost_input_1k': 0.5, 'status': 'active',
        })
        b = self.env['ai.model'].create({
            'name': 'b', 'provider': p.id, 'sys_multiplier': 1.0,
            'cost_input_1k': 0.1, 'status': 'active',
        })
        chosen = self._cheapest_query()
        self.assertEqual(chosen.id, b.id)

    def test_activity_idempotent(self):
        coworker = self.env['ai.coworker'].create({'name': 'Cw'})
        p = self.env['ai.provider'].create({
            'name': 'P', 'provider_type': 'custom', 'base_url': 'http://x/v1',
        })
        model = self.env['ai.model'].create({
            'name': 'cheap', 'provider': p.id, 'status': 'active',
        })
        self.assertTrue(coworker._activity_check_model_assignment(model))
        self.assertFalse(coworker._activity_check_model_assignment(model))
        activities = self.env['mail.activity'].sudo().search([
            ('res_model', '=', 'ai.coworker'), ('res_id', '=', coworker.id),
            ('active', '=', True),
        ])
        self.assertEqual(len(activities), 1)

    def test_resolve_default_model_no_hardcode(self):
        """_ensure_agent utan default och utan modeller → agent utan model_id,
        ingen hårdkodad '365'."""
        coworker = self.env['ai.coworker'].create({'name': 'Cw2'})
        coworker._ensure_agent()
        agent = coworker.agent_ids[0].agent_id
        self.assertFalse(agent.model_id)

    def test_chat_without_model_raises(self):
        from odoo.addons.ai_agent_core.core.provider import (
            AIProvider, ProviderError)
        provider = AIProvider(base_url='http://x/v1')
        with self.assertRaises(ProviderError):
            _run(provider.chat(
                model='', messages=[]))

    def test_no_hardcoded_fallbacks(self):
        import os
        import odoo.addons.ai_agent_core as mod_pkg
        root = os.path.dirname(mod_pkg.__file__)
        banned = ['cerebras/gpt-oss-120b', "'365'"]
        hits = []
        for dirpath, _dirs, files in os.walk(root):
            if 'tests' in dirpath or '.git' in dirpath:
                continue
            for fname in files:
                if not fname.endswith('.py'):
                    continue
                path = os.path.join(dirpath, fname)
                for i, line in enumerate(open(path, encoding='utf-8'), 1):
                    if any(b in line for b in banned):
                        hits.append(f'{path}:{i}')
        self.assertEqual(hits, [])


class TestEffectiveApiKey(ConfigParamGuardedCase):
    """provider-key-fallback D1: nyckeluppslagets fyra scenarier.

    Ordningen är kontraktet: record → ai_provider_api_key (odoo.conf) →
    ingen nyckel. Testerna
    pinnar hela ordningen, inte bara att fallbacken finns — det var
    avsaknaden av en gemensam ordning som gjorde att de fem anropsvägarna
    drev isär.
    """

    def setUp(self):
        super().setUp()
        from odoo.tools import config as odoo_config
        self._odoo_config = odoo_config
        self._saved = {
            k: odoo_config.get(k) for k in (
                'ai_provider_api_key', 'ai_provider_endpoint')
        }
        self.ICP = self.env['ir.config_parameter'].sudo()
        self.ICP.set_param('bifrost.admin_api_key', '')
        self.config_key = 'sk-bf-config-0000-1111-2222-333344445555'

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                self._odoo_config.pop(k, None)
            else:
                self._odoo_config[k] = v
        super().tearDown()

    def _provider(self, **kw):
        vals = {
            'name': 'P', 'provider_type': 'custom',
            'base_url': 'http://192.0.2.77:9999/v1',
        }
        vals.update(kw)
        return self.env['ai.provider'].create(vals)

    def test_config_key_used_when_record_empty(self):
        """Tomt api_key → ai_provider_api_key ur odoo.conf används."""
        self._odoo_config['ai_provider_api_key'] = self.config_key
        p = self._provider(api_key='')
        self.assertEqual(p._effective_api_key(), self.config_key)
        self.assertEqual(p._key_source(), 'config')
        # Uppslaget är härlett: recordet skrivs inte om.
        self.assertFalse(p.api_key)

    def test_own_key_wins_over_config(self):
        """Egen nyckel på recordet vinner över odoo.conf."""
        self._odoo_config['ai_provider_api_key'] = self.config_key
        own = 'sk-bf-vk-egen-aaaa-bbbb-cccc-ddddeeeeffff'
        p = self._provider(api_key=own)
        self.assertEqual(p._effective_api_key(), own)
        self.assertEqual(p._key_source(), 'record')

    def test_admin_key_is_never_used(self):
        """bifrost.admin_api_key ingår INTE i uppslaget — för någon typ.

        Nyckeluppslaget är generiskt: record → odoo.conf → fel. Admin-
        nyckeln används av /admin/*-ytan och ska inte kunna bli en providers
        anropsnyckel via en bakväg.
        """
        self._odoo_config['ai_provider_api_key'] = ''
        self.ICP.set_param('bifrost.admin_api_key', 'sk-bf-admin-only')
        for typ, bifrost in (('custom', False), ('bifrost', True)):
            p = self._provider(
                provider_type=typ, is_bifrost=bifrost, api_key='')
            self.assertEqual(p._effective_api_key(), '',
                             '%s fick admin-nyckeln' % typ)
            self.assertEqual(p._key_source(), 'none')

    def test_no_key_anywhere(self):
        """Ingen nyckel någonstans → tom sträng och källan 'none'."""
        self._odoo_config['ai_provider_api_key'] = ''
        p = self._provider(api_key='')
        self.assertEqual(p._effective_api_key(), '')
        self.assertEqual(p._key_source(), 'none')

    def test_not_required_gates_the_config_key(self):
        """is_key_required=False → INGEN nyckel används, inte ens ur conf.

        Detta är flaggans hela poäng: en lokal Ollama ska inte skicka
        Bifrosts VK till localhost. Utan gaten blev `is_key_required`
        verkningslös och varje nyckellös provider fick conf-nyckeln på
        köpet.
        """
        self._odoo_config['ai_provider_api_key'] = self.config_key
        p = self._provider(api_key='', is_key_required=False)
        self.assertEqual(p._effective_api_key(), '',
                         'conf-nyckeln läckte till en provider utan nyckelkrav')
        self.assertEqual(p._key_source(), 'not-required')

    def test_not_required_gates_the_record_key_too(self):
        """Även en nyckel på recordet ignoreras när flaggan är av."""
        self._odoo_config['ai_provider_api_key'] = ''
        p = self._provider(api_key='sk-satt-men-avstangd',
                           is_key_required=False)
        self.assertEqual(p._effective_api_key(), '')
        self.assertEqual(p._key_source(), 'not-required')

    def test_required_uses_config_key(self):
        """is_key_required=True → conf-nyckeln används (regressionen)."""
        self._odoo_config['ai_provider_api_key'] = self.config_key
        p = self._provider(api_key='', is_key_required=True)
        self.assertEqual(p._effective_api_key(), self.config_key)
        self.assertEqual(p._key_source(), 'config')


class TestFetchModelsWithConfigKey(ConfigParamGuardedCase):
    """provider-key-fallback D5: import validerar mot EFFEKTIV nyckel.

    Regressionen: `action_fetch_models` krävde `self.api_key`, så en provider
    vars nyckel bara fanns i odoo.conf avvisades med "API key required" och
    katalogen uppdaterades aldrig. Testet mockar HTTP-lagret — det som prövas
    är valideringen och att rätt nyckel når headern, inte nätverket.
    """

    def setUp(self):
        super().setUp()
        from odoo.tools import config as odoo_config
        self._odoo_config = odoo_config
        # Nolla ai_provider_list: detta test provar NYCKELN, inte listan.
        self._saved = {k: odoo_config.get(k) for k in (
            'ai_provider_api_key', 'ai_provider_list')}
        self._odoo_config['ai_provider_list'] = ''
        self.config_key = 'sk-bf-config-import-test'

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                self._odoo_config.pop(k, None)
            else:
                self._odoo_config[k] = v
        super().tearDown()

    def test_fetch_models_uses_config_key(self):
        """Tomt api_key + nyckel i odoo.conf → importen körs och lyckas."""
        from unittest.mock import patch, MagicMock
        self._odoo_config['ai_provider_api_key'] = self.config_key
        provider = self.env['ai.provider'].create({
            'name': 'Config Key Provider',
            'provider_type': 'custom',
            'base_url': 'http://192.0.2.88:9999/v1',
            'api_key': '',
            'is_key_required': True,
        })
        sent = {}

        def fake_urlopen(req, timeout=None, context=None):
            sent['headers'] = dict(req.headers)
            resp = MagicMock()
            resp.read.return_value = json.dumps(
                {'data': [{'id': 'test-model-a'}]}).encode()
            resp.__enter__ = lambda self_: resp
            resp.__exit__ = lambda self_, *a: False
            return resp

        with patch('urllib.request.urlopen', fake_urlopen), \
                patch.object(type(provider), '_fetch_config_map',
                             lambda self_: {}):
            provider.action_fetch_models()

        auth = {k.lower(): v for k, v in sent['headers'].items()}.get(
            'authorization', '')
        self.assertIn(self.config_key, auth,
                      'config-nyckeln ska nå Authorization-headern')
        self.assertTrue(
            self.env['ai.model'].search_count([
                ('provider', '=', provider.id), ('name', '=', 'test-model-a')]),
            'modellen ska ha importerats')

    def test_fetch_models_without_any_key_names_both_sources(self):
        """Ingen nyckel någonstans → felet namnger båda källorna."""
        from odoo.exceptions import UserError
        self._odoo_config['ai_provider_api_key'] = ''
        provider = self.env['ai.provider'].create({
            'name': 'Keyless Provider',
            'provider_type': 'custom',
            'base_url': 'http://192.0.2.89:9999/v1',
            'api_key': '',
            'is_key_required': True,
        })
        with self.assertRaises(UserError) as ctx:
            provider.action_fetch_models()
        msg = str(ctx.exception)
        self.assertIn('ai_provider_api_key', msg)
        self.assertIn('odoo.conf', msg)


class TestEmptyCatalogIsAnError(ConfigParamGuardedCase):
    """Tom katalog på en nyckelförsedd provider är ett FEL (drift 2026-11-16).

    En gateway svarar 200 med `{"data": []}` när nyckeln autentiserar men
    inte är knuten till några modeller/combos. Symptomet var: `key_state`
    grönt, "Models Synced: 0 models", ingen ledtråd. Den verkliga orsaken
    (403 combo_forbidden) syns först när man ringer modellen.

    Tysta nollor är den sortens "ser ut som att det fungerar" som kostade
    två dagar i FYND 2026-10-02.
    """

    def setUp(self):
        super().setUp()
        from odoo.tools import config as odoo_config
        self._odoo_config = odoo_config
        # BADA nycklarna: testet provar felvagen nar varken katalog eller
        # ai_provider_list finns. Utan att nolla listan lacker maskinens
        # odoo.conf in och testet importerar conf-listan i stallet.
        self._saved = {k: odoo_config.get(k) for k in (
            'ai_provider_api_key', 'ai_provider_list')}
        self._odoo_config['ai_provider_list'] = ''
        self.config_key = 'sk-bf-oregistrerad-nyckel'

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                self._odoo_config.pop(k, None)
            else:
                self._odoo_config[k] = v
        super().tearDown()

    def _provider(self, **kw):
        vals = {
            'name': 'Empty Catalog Test', 'provider_type': 'bifrost',
            'base_url': 'http://192.0.2.123:8081/v1',
            'is_bifrost': True, 'api_key': '', 'is_key_required': True,
        }
        vals.update(kw)
        return self.env['ai.provider'].create(vals)

    def _mock_urlopen(self, payload):
        from unittest.mock import patch, MagicMock

        def fake(req, timeout=None, context=None):
            resp = MagicMock()
            resp.read.return_value = json.dumps(payload).encode()
            resp.__enter__ = lambda self_: resp
            resp.__exit__ = lambda self_, *a: False
            return resp
        return patch('urllib.request.urlopen', fake)

    def test_empty_catalog_with_key_raises(self):
        """200 med tom data + nyckel → UserError som namnger orsaken."""
        from odoo.exceptions import UserError
        self._odoo_config['ai_provider_api_key'] = self.config_key
        p = self._provider()
        with self._mock_urlopen({'object': 'list', 'data': []}):
            with self.assertRaises(UserError) as ctx:
                p.action_fetch_models()
        msg = str(ctx.exception)
        self.assertIn('no models', msg)
        self.assertIn('not entitled', msg)
        self.assertIn('config', msg, 'felkällan ska anges')

    def test_empty_catalog_without_key_does_not_raise(self):
        """Nyckellös provider (t.ex. Ollama) med tom katalog är inte ett fel."""
        self._odoo_config['ai_provider_api_key'] = ''
        p = self._provider(
            name='Local', provider_type='ollama', is_bifrost=False,
            base_url='http://localhost:11434/v1', is_key_required=False)
        with self._mock_urlopen({'object': 'list', 'data': []}):
            count = p.action_fetch_models()['params']['message']
        self.assertIn('0 models', count)

    def test_drift_check_skips_empty_catalog(self):
        """Driftkontrollen ska säga "kunde inte", inte "allt är borta"."""
        self._odoo_config['ai_provider_api_key'] = self.config_key
        p = self._provider()
        with self._mock_urlopen({'object': 'list', 'data': []}):
            drift = p._catalog_drift()
        self.assertFalse(drift['checked'],
                         'tom katalog får inte räknas som genomförd kontroll')
        self.assertEqual(drift['missing_in_catalog'], [])


class TestProviderListFromConfig(ConfigParamGuardedCase):
    """`ai_provider_list` är kontraktet för vilka modeller som ska importeras.

    Drift 2026-11-16: conf angav `ai_provider_list = OR+deepseek,OR+deepseek1`
    men katalogen `/v1/models` svarade 200 med tom lista (combo-adaptern
    filtrerar på entitlements). Resultatet blev "0 modeller" trots att
    conf uttryckligen angav två combos.

    Listan lästes aldrig av koden — den var dokumentation. Nu är den
    kontraktet: conf vinner över en tom/filtrerad katalog.
    """

    def setUp(self):
        super().setUp()
        from odoo.tools import config as odoo_config
        self._odoo_config = odoo_config
        self._keys = ('ai_provider_api_key', 'ai_provider_list')
        self._saved = {k: odoo_config.get(k) for k in self._keys}
        self._odoo_config['ai_provider_api_key'] = 'sk-bf-test'

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                self._odoo_config.pop(k, None)
            else:
                self._odoo_config[k] = v
        super().tearDown()

    def _provider(self, **kw):
        vals = {
            'name': 'List Provider', 'provider_type': 'bifrost',
            'base_url': 'http://192.0.2.200:8081/v1',
            'is_bifrost': True, 'api_key': '', 'is_key_required': True,
        }
        vals.update(kw)
        return self.env['ai.provider'].create(vals)

    def _mock(self, payload):
        from unittest.mock import patch, MagicMock

        def fake(req, timeout=None, context=None):
            resp = MagicMock()
            resp.read.return_value = json.dumps(payload).encode()
            resp.__enter__ = lambda self_: resp
            resp.__exit__ = lambda self_, *a: False
            return resp
        return patch('urllib.request.urlopen', fake)

    def _patch_config_map(self):
        """Hoppa över /v1/config-hämtningen (prövar inte den här)."""
        from unittest.mock import patch
        from odoo.addons.ai_agent_core.models.ai_provider import AIProvider
        return patch.object(AIProvider, '_fetch_config_map', return_value={})

    def test_config_list_parsed(self):
        """Listan kommasepareras och trimmas."""
        self._odoo_config['ai_provider_list'] = 'OR+deepseek, OR+deepseek1 ,cheap'
        self.assertEqual(
            self.env['ai.provider']._config_model_list(),
            ['OR+deepseek', 'OR+deepseek1', 'cheap'])

    def test_config_list_empty(self):
        self._odoo_config['ai_provider_list'] = ''
        self.assertEqual(self.env['ai.provider']._config_model_list(), [])

    def test_empty_catalog_imports_from_config_list(self):
        """Tom katalog + lista i conf → listan importeras (inte 0 modeller)."""
        self._odoo_config['ai_provider_list'] = 'OR+deepseek,OR+deepseek1'
        p = self._provider()
        with self._mock({'object': 'list', 'data': []}), \
                self._patch_config_map():
            p.action_fetch_models()
        names = sorted(self.env['ai.model'].search(
            [('provider', '=', p.id)]).mapped('name'))
        self.assertEqual(names, ['OR+deepseek', 'OR+deepseek1'],
                         'conf-listan ska ha importerats')

    def test_catalog_is_narrowed_by_config_list(self):
        """Katalog med fler modeller än conf → conf vinner."""
        self._odoo_config['ai_provider_list'] = 'OR+deepseek'
        p = self._provider()
        with self._mock({'object': 'list', 'data': [
                {'id': 'OR+deepseek'}, {'id': 'cheap'}, {'id': 'moderate'}]}), \
                self._patch_config_map():
            p.action_fetch_models()
        names = self.env['ai.model'].search(
            [('provider', '=', p.id)]).mapped('name')
        self.assertEqual(names, ['OR+deepseek'])

    def test_empty_catalog_without_list_raises(self):
        """Varken katalog eller lista → fel som namnger båda vägarna."""
        from odoo.exceptions import UserError
        self._odoo_config['ai_provider_list'] = ''
        p = self._provider()
        with self._mock({'object': 'list', 'data': []}):
            with self.assertRaises(UserError) as ctx:
                p.action_fetch_models()
        msg = str(ctx.exception)
        self.assertIn('ai_provider_list', msg)
        self.assertIn('no models', msg)
