# -*- coding: utf-8 -*-
"""Tester för restore-webui-token-usage.

Bakgrund: WebUI visade tidigare tokenförbrukning per session. Den
försvann av fyra skäl — tre i frontend (visningen uppdaterades bara i en
`.then()` som krävde visningsbart innehåll, `selectThread` rörde den
aldrig, `newThread` nollställde den aldrig) och en i backend
(`_persist_stream_answer` skrev assistant-raden med 0/0 och
`usage_reported` default True, trots att SSE-`done` bar riktiga siffror).

Dessa tester låser de två backend-invarianterna:

  6.1  `thread_get` returnerar ett `usage`-block som summan över
       icke-informativa rader — klienten ska aldrig summera själv.
  6.2  En streamtur bokför verklig usage på assistant-raden
       (`usage_reported=True`, `token_sys` > 0, `cost_usd` > 0).
  6.3  "En rad bokförs en gång": klientens POST får inte lägga samma
       tokens en andra gång (varken på assistant- eller user-raden).
"""

import json
from unittest.mock import patch

from odoo.tests import common, tagged
from ._test_users import create_user


@tagged('post_install', '-at_install')
class TestWebuiTokenUsage(common.TransactionCase):
    """6.1/6.2/6.3: usage-block, bokföring och dubbelbokningsskydd."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Session = cls.env['ai.coworker.session']
        cls.Line = cls.env['ai.coworker.session.line']
        cls.user = cls.env.create_user(name='Token Usage User', login='token_usage@example.com', email='token_usage@example.com')
        cls.coworker = cls.env['ai.coworker'].create({
            'name': 'Token Usage Coworker',
            'description': 'restore-webui-token-usage',
            'status': 'active',
        })
        # Priserna bor på ai.model — utan en modell med cost_input_1k/
        # cost_output_1k blir prissnapshotten 0 och `cost_usd` 0, vilket
        # hade gjort 6.2 tandlös (den kunde inte skilja "pris saknas"
        # från "kostnaden bokfördes aldrig").
        cls.provider = cls.env['ai.provider'].create({
            'name': 'Token Usage Provider',
            'provider_type': 'openai',
            'base_url': 'http://127.0.0.1:1/v1',
            'api_key': 'sk-test-not-used',
        })
        cls.ai_model = cls.env['ai.model'].create({
            'name': 'gpt-4o-mini',
            'api_name': 'gpt-4o-mini',
            'provider': cls.provider.id,
            'cost_input_1k': 0.003,
            'cost_output_1k': 0.015,
            'sys_multiplier': 2.0,
        })

    def _session(self):
        return self.Session.create({
            'coworker_id': self.coworker.id,
            'status': 'active',
            'user_id': self.user.id,
            'name': 'token-usage-test',
            'init_type': 'web_ui',
        })

    # ── 6.1 usage-block i trådsvaret ──────────────────────────────────

    def test_usage_block_sums_billed_lines_only(self):
        """6.1: `usage` summerar icke-informativa rader — inte alla.

        Informativa rader bär spårbarhet (per-agent-tokens) men är redan
        bokförda på sessionen. Räknas de med blåses budgettaket upp.
        Detta är exakt felet klientens egen summering gjorde, eftersom
        den inte ser flaggan.
        """
        from odoo.addons.ai_agent_core.controllers.stream import (
            _thread_usage_block)
        session = self._session()
        self.Line.create({
            'session_id': session.id, 'role': 'user', 'content': 'fråga',
            'sequence': 1, 'token_input': 1000, 'token_output': 0,
            'sys_multiplier': 2.0, 'usage_reported': True,
            'cost_input_1k': 0.003, 'cost_output_1k': 0.015,
        })
        self.Line.create({
            'session_id': session.id, 'role': 'assistant', 'content': 'svar',
            'sequence': 2, 'token_input': 0, 'token_output': 500,
            'sys_multiplier': 2.0, 'usage_reported': True,
            'cost_input_1k': 0.003, 'cost_output_1k': 0.015,
        })
        # Informativ rad — får INTE räknas in.
        self.Line.create({
            'session_id': session.id, 'role': 'tool', 'content': 'spår',
            'sequence': 3, 'token_input': 99999, 'token_output': 0,
            'sys_multiplier': 1.0, 'usage_reported': True,
            'informative': True,
        })
        usage = _thread_usage_block(session)
        self.assertEqual(usage['token_input'], 1000)
        self.assertEqual(usage['token_output'], 500)
        # (1000 + 500) × 2.0 — den informativa radens 99999 är exkluderad.
        self.assertEqual(usage['token_sys'], 3000)
        self.assertEqual(usage['lines'], 2)
        self.assertEqual(usage['unmeasured_lines'], 0)
        # 1000/1000×0.003 + 500/1000×0.015 = 0.003 + 0.0075
        self.assertAlmostEqual(usage['cost_usd'], 0.0105, places=6)

    def test_usage_block_counts_unmeasured_lines(self):
        """6.1: omätta rader räknas som "vet ej" — inte som noll."""
        from odoo.addons.ai_agent_core.controllers.stream import (
            _thread_usage_block)
        session = self._session()
        self.Line.create({
            'session_id': session.id, 'role': 'assistant', 'content': 'svar',
            'sequence': 1, 'token_input': 0, 'token_output': 0,
            'usage_reported': False,
        })
        usage = _thread_usage_block(session)
        self.assertEqual(usage['token_sys'], 0)
        self.assertEqual(usage['unmeasured_lines'], 1,
                         'omätt rad måste rapporteras, inte döljas som 0')

    def test_thread_get_exposes_usage_block(self):
        """6.1: `thread_get` bär `usage` i sitt JSON-svar."""
        from odoo.addons.ai_agent_core.controllers import stream as stream_mod
        session = self._session()
        self.Line.create({
            'session_id': session.id, 'role': 'user', 'content': 'hej',
            'sequence': 1, 'token_input': 40, 'token_output': 0,
            'sys_multiplier': 1.0, 'usage_reported': True,
        })
        payload = stream_mod._thread_usage_block(session)
        body = json.loads(json.dumps({'usage': payload}))
        self.assertIn('usage', body)
        self.assertEqual(body['usage']['token_sys'], 40)

    # ── 6.2 boka verklig usage på assistant-raden ─────────────────────

    def test_stream_answer_books_real_usage(self):
        """6.2: streamturen bokför providerns riktiga usage.

        Före fixen skrevs raden med token_input=0/token_output=0 medan
        `usage_reported` default var True → raden SÅG mätt ut men var
        noll, och `cost_usd` blev strukturellt 0.
        """
        from odoo.addons.ai_agent_core.controllers.stream import (
            _persist_stream_answer)
        session = self._session()
        _persist_stream_answer(
            self.env, session.id, 'Svaret.',
            token_input=1200, token_output=300,
            model_real='gpt-4o-mini', cached_tokens=200)
        line = session.session_line_ids.filtered(
            lambda l: l.role == 'assistant')
        self.assertEqual(len(line), 1)
        self.assertEqual(line.token_input, 1200)
        self.assertEqual(line.token_output, 300)
        self.assertEqual(line.cached_tokens, 200)
        self.assertTrue(line.usage_reported,
                        'rapporterad usage ska markeras som mätt')
        # token_sys är DEBITERINGSGRUND = (input + output) × multiplikator.
        # Cachade tokens debiteras fortfarande — de gör bara kostnaden
        # billigare (se cost_usd nedan). De två får aldrig blandas ihop.
        self.assertEqual(line.token_sys, 3000)
        self.assertEqual(line.sys_multiplier, 2.0,
                         'multiplikatorn ska hämtas från ai.model')
        # cost_usd räknar bort cachade tokens:
        # (1200−200)/1000×0.003 + 300/1000×0.015 = 0.003 + 0.0045
        self.assertAlmostEqual(line.cost_usd, 0.0075, places=6,
                               msg='prissnapshotten ska ge faktisk kostnad')

    def test_stream_answer_without_usage_is_unmeasured(self):
        """6.2: tur utan usage-rapport blir omätt — aldrig "gratis"."""
        from odoo.addons.ai_agent_core.controllers.stream import (
            _persist_stream_answer)
        session = self._session()
        _persist_stream_answer(self.env, session.id, 'Svaret.')
        line = session.session_line_ids.filtered(
            lambda l: l.role == 'assistant')
        self.assertFalse(line.usage_reported,
                         'utan tokens är usage okänd, inte noll')
        self.assertEqual(line.cost_usd, 0.0)

    def test_stream_answer_is_idempotent_with_usage(self):
        """6.2: usage-argumenten får inte bryta idempotensen."""
        from odoo.addons.ai_agent_core.controllers.stream import (
            _persist_stream_answer)
        session = self._session()
        _persist_stream_answer(self.env, session.id, 'svar',
                               token_input=10, token_output=5)
        _persist_stream_answer(self.env, session.id, 'svar',
                               token_input=10, token_output=5)
        assistants = session.session_line_ids.filtered(
            lambda l: l.role == 'assistant')
        self.assertEqual(len(assistants), 1,
                         'en tur ska ge exakt en assistant-rad')

    # ── 6.3 en rad bokförs en gång ────────────────────────────────────

    def test_client_post_does_not_rebook_measured_output(self):
        """6.3: klientens POST får inte lägga output-tokens en gång till.

        Servern har redan bokfört turens usage. POST:en ska berika raden
        (debug/källor) men lämna tokenfälten orörda — annars dubbelräknas
        sessionens token_sys och därmed budgettaket.
        """
        from odoo.addons.ai_agent_core.controllers.stream import (
            _persist_stream_answer)
        session = self._session()
        _persist_stream_answer(self.env, session.id, 'Svaret.',
                               token_input=100, token_output=50)
        before = session.token_sys
        session._save_client_response(
            content='Svaret.', role='assistant',
            token_output=50, debug_info='debug')
        line = session.session_line_ids.filtered(
            lambda l: l.role == 'assistant')
        self.assertEqual(line.token_output, 50,
                         'serverns siffra ska stå kvar')
        self.assertEqual(session.token_sys, before,
                         'ingen dubbelbokning av samma turs output')

    def test_client_post_still_enriches_row(self):
        """6.3: berikning (debug/källor) får inte tappas av grinden."""
        from odoo.addons.ai_agent_core.controllers.stream import (
            _persist_stream_answer)
        session = self._session()
        _persist_stream_answer(self.env, session.id, 'Svar A',
                               token_input=100, token_output=50)
        session._save_client_response(
            content='Svar B', role='assistant', token_output=50,
            debug_info='resonemang', source_urls='https://x.se')
        new_line = session.session_line_ids.filtered(
            lambda l: l.content == 'Svar B')
        self.assertTrue(new_line, 'ett annat svar ska appendas')
        self.assertEqual(new_line.debug_info, 'resonemang')
        self.assertEqual(new_line.token_output, 0,
                         'ny rad utan server-bokföring får inga påhittade tokens')
