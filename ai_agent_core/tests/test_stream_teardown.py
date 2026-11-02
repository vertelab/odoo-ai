# -*- coding: utf-8 -*-
"""Tester för handler- och provider-livscykler (D3, D5).

VARFÖR DESSA TESTER FINNS: `generate()` i `controllers/stream.py` är en
generator. Klienten kan koppla ner vid vilket `yield` som helst — då kastas
`GeneratorExit` och allt EFTER loopen hoppas över. Låg avregistreringen där
blev handlern kvar i `_webui_handlers` för evigt, en per avbruten stream.

Samma mönster gäller providern: `asyncio.run()` stänger event-loopen men inte
httpx-klienten, som lämnar en pending task ("Task was destroyed but it is
pending!"). `aclose()` måste köras i `finally` — även när anropet kastar.
"""

from unittest.mock import MagicMock, patch

from odoo.tests.common import TransactionCase, tagged

from odoo.addons.ai_agent_core.controllers.stream import (
    _register_webui_handler,
    _unregister_webui_handler,
    _unregister_webui_handler_safely,
    _webui_handlers,
)


@tagged('ai_core', 'post_install', '-at_install')
class TestWebuiHandlerTeardown(TransactionCase):
    """3.1 + 3.2: avregistreringen sker i finally och är idempotent."""

    def test_unregister_safely_removes_key(self):
        """3.1: nyckeln finns inte kvar efter städningen."""
        _register_webui_handler('teardown-test-uuid', MagicMock())
        self.assertIn('teardown-test-uuid', _webui_handlers)

        _unregister_webui_handler_safely(
            {'session_uuid': 'teardown-test-uuid'})

        self.assertNotIn(
            'teardown-test-uuid', _webui_handlers,
            'handlern ska vara avregistrerad efter finally-städningen')

    def test_unregister_safely_is_idempotent(self):
        """3.2: lycklig väg följt av finally får inte kasta."""
        _register_webui_handler('idempotent-uuid', MagicMock())
        # Lycklig väg (snabbvägen i _stream) …
        _unregister_webui_handler('idempotent-uuid')
        # … följt av finally-städningen. Ska vara en no-op.
        _unregister_webui_handler_safely({'session_uuid': 'idempotent-uuid'})
        self.assertNotIn('idempotent-uuid', _webui_handlers)

    def test_unregister_safely_survives_missing_key(self):
        """Städning innan strömmen hunnit registrera sig får inte kasta."""
        _unregister_webui_handler_safely({'session_uuid': None})
        _unregister_webui_handler_safely({})
        _unregister_webui_handler_safely({'session_uuid': 'aldrig-registrerad'})

    def test_unregister_safely_swallows_pop_errors(self):
        """En trasig pop får inte bli 'generator ignored GeneratorExit'."""
        with patch(
            'odoo.addons.ai_agent_core.controllers.stream'
            '._unregister_webui_handler',
            side_effect=RuntimeError('boom'),
        ):
            # Får inte kasta vidare.
            _unregister_webui_handler_safely({'session_uuid': 'x'})


@tagged('ai_core', 'post_install', '-at_install')
class TestProviderClosedOnError(TransactionCase):
    """3.3 + 3.4: providern stängs även när anropet kastar."""

    def test_final_summary_closes_provider_on_error(self):
        """3.3: `_run_final_summary_llm` går via `_run_provider`."""
        from odoo.addons.ai_agent_core.core.provider import _run_provider

        closed = []

        async def _chat(**kwargs):
            raise RuntimeError('provider-fel')

        async def _aclose():
            closed.append(True)

        provider = MagicMock()
        provider.chat = _chat
        provider.aclose = _aclose

        with self.assertRaises(Exception):
            _run_provider(provider.chat(messages=[]), provider)
        self.assertTrue(closed, 'provider.aclose måste anropas i finally')

    def test_extract_concepts_closes_provider_on_error(self):
        """3.4: `_extract_concepts_from_conversation` har egen try/finally."""
        import inspect
        from odoo.addons.ai_agent_core.models.ai_coworker import AICoworker

        src = inspect.getsource(
            AICoworker._extract_concepts_from_conversation)
        self.assertIn('finally', src,
                      'providern måste stängas i finally')
        self.assertIn('aclose', src,
                      'finally måste anropa aclose')
