# -*- coding: utf-8 -*-
"""B7 — provider-livscykel: `aclose()` måste köras vid varje synkron körning.

Bakgrund: nio callsites körde `asyncio.run(loop.run(prompt))` utan att stänga
providern. `asyncio.run()` stänger event-loopen, men httpx-klienten lever
vidare med en pending connection-task. Odoo-loggen fylldes av:

    Task was destroyed but it is pending!
    task: <Task pending name='...' coro=<<async_generator_athrow ...>>

Bara koncept-extraktionen i `ai_coworker.py` stängde korrekt — den byggde en
egen `_run_and_close()`-wrapper. Rättningen samlar mönstret i
`core.provider._run_provider(coro, provider)`.

Detta test bevakar två saker:
  1. att hjälparen faktiskt anropar `aclose()` — även när coroutinen kastar,
  2. att inga callsites har återgått till naken `asyncio.run()`.
"""

import ast
import os
import textwrap

from odoo.tests.common import TransactionCase, tagged


def _module_root():
    from odoo.addons.ai_agent_core import __file__ as pkg_file
    return os.path.dirname(pkg_file)


def _source(rel_path):
    with open(os.path.join(_module_root(), rel_path), encoding='utf-8') as fh:
        return fh.read()


@tagged('ai_core', 'post_install', '-at_install')
class TestRunProviderClosesClient(TransactionCase):
    """13.1: `_run_provider` stänger providern — även vid undantag."""

    def _fake_provider(self, close_log):
        class _FakeProvider:
            async def aclose(self):
                close_log.append('closed')
        return _FakeProvider()

    def test_closes_after_success(self):
        from odoo.addons.ai_agent_core.core.provider import _run_provider

        close_log = []
        provider = self._fake_provider(close_log)

        async def _work():
            return 'resultat'

        result = _run_provider(_work(), provider)
        self.assertEqual(result, 'resultat')
        self.assertEqual(close_log, ['closed'],
                         'providern ska stängas efter lyckad körning')

    def test_closes_after_exception(self):
        from odoo.addons.ai_agent_core.core.provider import _run_provider

        close_log = []
        provider = self._fake_provider(close_log)

        async def _boom():
            raise ValueError('provider-fel')

        with self.assertRaises(ValueError):
            _run_provider(_boom(), provider)
        self.assertEqual(close_log, ['closed'],
                         'providern ska stängas även när coroutinen kastar')

    def test_tolerates_none_provider(self):
        from odoo.addons.ai_agent_core.core.provider import _run_provider

        async def _work():
            return 42

        self.assertEqual(_run_provider(_work(), None), 42)

    def test_aclose_failure_does_not_mask_result(self):
        """Städning får aldrig kasta — resultatet ska ändå komma ut."""
        from odoo.addons.ai_agent_core.core.provider import _run_provider

        class _BrokenProvider:
            async def aclose(self):
                raise RuntimeError('aclose trasig')

        async def _work():
            return 'ok'

        self.assertEqual(_run_provider(_work(), _BrokenProvider()), 'ok')


@tagged('ai_core', 'post_install', '-at_install')
class TestNoNakedAsyncioRun(TransactionCase):
    """13.3/13.4: strukturell grind mot återfall."""

    #: Filer där naken `asyncio.run()` är tillåtet, med skäl.
    ALLOWED = {
        # Kör verktygskod, inte LLM-anrop — ingen provider, ingen httpx-klient.
        'models/ai_tool.py',
        # Har egen _run_and_close()-wrapper som redan stänger providern.
        'models/ai_coworker.py',
        # Hjälparens egen implementation.
        'core/provider.py',
        # Kanal-körningen (channel-adapter-wiring 3.1): `run_coroutine_sync`
        # kör `process_item()` från synkron Odoo-kod. Ingen provider-klient
        # ägs av anropet — providern skapas och stängs inne i loopen.
        'core/channels.py',
        # NATS KV-låsen (create/delete): rena nats-anrop, ingen LLM-provider
        # och därmed ingen httpx-klient att lämna pending.
        'core/tools.py',
    }

    def test_no_unclosed_asyncio_run_in_models(self):
        root = _module_root()
        offenders = []
        for dirpath, _dirnames, filenames in os.walk(root):
            base = os.path.basename(dirpath)
            if base in ('tests', '__pycache__'):
                continue
            for fname in filenames:
                if not fname.endswith('.py'):
                    continue
                path = os.path.join(dirpath, fname)
                rel = os.path.relpath(path, root).replace(os.sep, '/')
                if rel in self.ALLOWED:
                    continue
                with open(path, encoding='utf-8') as fh:
                    src = fh.read()
                if 'asyncio.run(' in src:
                    offenders.append(rel)

        self.assertEqual(
            offenders, [],
            'B7: dessa filer kör asyncio.run() utan att stänga providern — '
            'använd `_run_provider(coro, provider)` i stället: %s'
            % offenders)

    def test_callsites_use_the_helper(self):
        """De sex tidigare trasiga callsites ska nu anropa hjälparen."""
        expected = [
            'models/ai_onboarding.py',
            'models/res_company.py',
            'models/ai_personal_memory.py',
            'models/ai_session.py',
        ]
        for rel in expected:
            src = _source(rel)
            self.assertIn(
                '_run_provider(', src,
                'B7: %s använder inte _run_provider' % rel)


@tagged('ai_core', 'post_install', '-at_install')
class TestRunningLoopGuardStillWorks(TransactionCase):
    """13.4: `_execute_tool` ska fortfarande neka i async-kontext."""

    def test_execute_tool_refuses_inside_running_loop(self):
        import asyncio
        from odoo.exceptions import UserError

        tool = self.env['ai.tool'].create({
            'name': 'B7 guard tool',
            'description': 'Guard-test: nekar i async-kontext',
            'code': 'return "hej"',
        })

        async def _inside_loop():
            with self.assertRaises(UserError):
                tool._execute_tool({})

        asyncio.run(_inside_loop())
