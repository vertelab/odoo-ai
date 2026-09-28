# -*- coding: utf-8 -*-
"""Regressionstester: openai_api-vägen utan pi_session_id (kompaktering).

Bakgrund (openai-api-kontextfonster-och-kompaktering):
Pi:s auto-kompaktering skickar en sammanfattnings-request som INTE passerar
`before_provider_request`-hooken och därför saknar `pi_session_id`. Odoo
svarade tidigare med en HTML-500 (`AttributeError: 'AIOpenAIAPI' object has
no attribute 'env'`). Dessa tester låser att:

  1. En request utan `pi_session_id` aldrig ger 500 — alltid giltig JSON.
  2. Ett fel i markör-extraktionen inte fäller requesten.
  3. `pi_session_id` kopplar sessionen 1:1 (skapa + återanvänd).
  4. `{pi session: <uuid>}`-markören fungerar när body-fältet saknas.
  5. Kontextfönstret (`context_window`) härleds/annonseras korrekt.

Testerna är medvetet uppdelade:
  * HTTP-tester (`HttpCase`) kontrollerar ENDAST svaret — statuskod och att
    body är giltig JSON. De skriver inget till DB och läser ingen DB-state
    skapad i en annan transaktion (HttpCase kör requesten i en egen cursor).
  * Modell-tester (`TransactionCase`) kontrollerar session-koppling och
    kontextfönster direkt via ORM:en.

Körs med:
    sudo checkmodule -d <db> -m ai_agent_core -t
"""

import json
import unittest
from unittest.mock import patch

from odoo.tests.common import HttpCase, TransactionCase, tagged


class _OpenAIAPIHTTPBase(HttpCase):
    """Bas för HTTP-tester mot /ai/v1/* (svarsobservation, ingen DB-state)."""

    def _post_chat(self, body, raw=None):
        return self.url_open(
            '/ai/v1/chat/completions',
            data=raw if raw is not None else json.dumps(body),
            headers={'Content-Type': 'application/json'},
        )

    @staticmethod
    def _assert_valid_json(test, resp):
        """Svaret ska vara giltig JSON — aldrig en HTML-500-sida."""
        test.assertNotEqual(resp.status_code, 500, resp.text[:500])
        test.assertNotIn('<!doctype html>', resp.text.lower())
        payload = json.loads(resp.text)  # kastar vid ogiltig JSON
        test.assertIsInstance(payload, dict)
        return payload


@tagged('post_install', '-at_install')
class TestCompactionWithoutSession(_OpenAIAPIHTTPBase):
    """Kompakterings-request utan pi_session_id får aldrig 500."""

    def test_no_session_no_500(self):
        """Request utan pi_session_id → giltig JSON (inte HTML-500)."""
        resp = self._post_chat({
            'model': 'nonexistent-coworker-for-json-test',
            'stream': False,
            'messages': [
                {'role': 'user', 'content': 'Summarize this conversation.'},
            ],
        })
        payload = self._assert_valid_json(self, resp)
        # Okänd coworker → 404 i OpenAI-format (inte 500)
        self.assertEqual(resp.status_code, 404, resp.text[:500])
        self.assertIn('error', payload)

    def test_invalid_json_body_gives_400_not_500(self):
        """Trasig JSON-body → 400 med JSON, inte HTML-500."""
        resp = self._post_chat(None, raw='{not valid json')
        payload = self._assert_valid_json(self, resp)
        self.assertEqual(resp.status_code, 400, resp.text[:500])
        self.assertIn('error', payload)

    def test_missing_model_gives_400_not_500(self):
        """Body utan model-fält → 400 med JSON, inte HTML-500."""
        resp = self._post_chat({'stream': False, 'messages': []})
        payload = self._assert_valid_json(self, resp)
        self.assertEqual(resp.status_code, 400, resp.text[:500])
        self.assertIn('error', payload)

    def test_non_string_model_does_not_crash(self):
        """model som icke-sträng (t.ex. tal) → JSON-fel, inte 500."""
        resp = self._post_chat({'model': 12345, 'stream': False,
                                'messages': []})
        self._assert_valid_json(self, resp)

    def test_marker_extraction_error_does_not_fail_request(self):
        """Fel i markör-extraktionen fäller inte requesten (JSON, inte 500)."""
        with patch(
            'odoo.addons.ai_agent_core.models.ai_session.AICoworkerSession'
            '._extract_pi_session_marker',
            side_effect=RuntimeError('boom'),
        ):
            resp = self._post_chat({
                'model': 'nonexistent-coworker-for-json-test',
                'stream': False,
                'messages': [{'role': 'user', 'content': 'Summarize.'}],
            })
        self._assert_valid_json(self, resp)


@tagged('post_install', '-at_install')
class TestPiSessionLinkingModel(TransactionCase):
    """pi_session_id kopplar sessionen 1:1 (skapa + återanvänd) — ORM-nivå."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Session = cls.env['ai.coworker.session']
        cls.coworker = cls.env['ai.coworker'].create({
            'name': 'Compaction Link Coworker',
            'status': 'active',
        })
        cls.env['ai.coworker.init_type'].create({
            'coworker_id': cls.coworker.id,
            'init_type': 'openai_api',
            'enabled': True,
        })

    def _find_or_create(self, pi_session_id=''):
        return self.Session._find_or_create_coworker_session(
            self.coworker.id, self.env.user.id,
            pi_session_id=pi_session_id, prompt='hi')[0]

    def test_session_created_with_pi_session_id(self):
        uuid = '11111111-2222-3333-4444-555555555555'
        sess = self._find_or_create(uuid)
        self.assertTrue(sess)
        self.assertEqual(sess.pi_session_id, uuid)
        self.assertEqual(sess.init_type, 'openai_api')

    def test_same_pi_session_id_reuses_session(self):
        uuid = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'
        first = self._find_or_create(uuid)
        second = self._find_or_create(uuid)
        self.assertEqual(first, second,
                         'Samma pi_session_id ska återanvända samma session')
        found = self.Session.search([('pi_session_id', '=', uuid)])
        self.assertEqual(len(found), 1)

    def test_marker_extraction_returns_uuid(self):
        """Markören `{pi session: <uuid>}` plockas ur text."""
        uuid = '99999999-8888-7777-6666-555555555555'
        got = self.Session._extract_pi_session_marker(
            f'You are helpful. {{pi session: {uuid}}}')
        self.assertEqual(got, uuid)

    def test_marker_missing_returns_empty(self):
        self.assertEqual(
            self.Session._extract_pi_session_marker('no marker here'), '')


@tagged('post_install', '-at_install')
class TestContextWindowEffective(TransactionCase):
    """Effektivt kontextfönster: manuellt slår härlett, auto följer modeller."""

    def setUp(self):
        super().setUp()
        self.Coworker = self.env['ai.coworker']
        self.InitType = self.env['ai.coworker.init_type']

    def _make_openai_init(self, coworker):
        return self.InitType.create({
            'coworker_id': coworker.id,
            'init_type': 'openai_api',
            'enabled': True,
        })

    def test_manual_value_wins(self):
        """Manuellt värde (200000) vinner över härlett."""
        coworker = self.Coworker.create({
            'name': 'Ctx Manual', 'status': 'active'})
        it = self._make_openai_init(coworker)
        it.openai_context_window = 200000
        it._inverse_openai_context_window()
        self.assertTrue(it.openai_context_window_manual)
        self.assertEqual(it._effective_context_window(), 200000)

    def test_manual_false_follows_models(self):
        """_manual=False → härleds från coworkerns agentmodeller."""
        coworker = self.Coworker.create({
            'name': 'Ctx Auto', 'status': 'active'})
        it = self._make_openai_init(coworker)
        it.openai_context_window_manual = False
        self.assertEqual(
            it._effective_context_window(),
            coworker._effective_context_window())

    def test_coworker_proxy_field_writes_to_init_type(self):
        """Coworker-formulärets proxy-fält skriver till init-typ-raden."""
        coworker = self.Coworker.create({
            'name': 'Ctx Proxy', 'status': 'active'})
        self._make_openai_init(coworker)
        coworker.write({'openai_context_window': 200000})
        it = coworker._get_active_init('openai_api')
        self.assertEqual(it.openai_context_window, 200000)
        self.assertTrue(it.openai_context_window_manual)
        self.assertEqual(it._effective_context_window(), 200000)

    def test_effective_context_window_positive(self):
        """Härlett värde är alltid ett positivt heltal."""
        coworker = self.Coworker.create({
            'name': 'Ctx Min', 'status': 'active'})
        self.assertGreater(coworker._effective_context_window(), 0)
