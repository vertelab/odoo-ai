# -*- coding: utf-8 -*-
"""Regressionstester: /ai/v1/tools/run — coworker-kontext (T/11533).

Bakgrund: endpointen `/ai/v1/tools/run` (stream.py, `tools_run`) körde
tidigare verktyg i FEL coworker-kontext när en explicit `coworker` skickades
men sessionen tillhörde en annan coworker. T/11527 löste prioriteringen
(steg 1: skapa/återanvänd en session bunden till den efterfrågade coworkern).
T/11533 gör avvikelsen synlig (steg 2) och separerar felmeddelandena
(steg 3).

Dessa tester låser:

  (a) `coworker` + `pi_session_id` → verktyget körs.
  (b) `coworker` utan session, där användarens senaste aktiva session
      tillhör en ANNAN coworker → verktyget körs ändå (skyddar steg 1).
  (c) okänt verktygsnamn → "Unknown tool 'X'".
  (d) verktyg som finns i ai.tool men tillhör en annan coworker →
      kontextspecifikt meddelande.

Testerna anropar `tools_run()` direkt (TransactionCase) med en fejkad
`request` — ingen HTTP-server behövs och API-nyckel-auth patchas bort
(testet handlar om coworker-prioritering, inte autentisering).

Körs med:
    sudo checkmodule -d <db> -m ai_agent_core -t
"""

import json
from unittest.mock import patch

from odoo.tests import common, tagged

# Modulnivå-import: samma modulobjekt som endpointen använder, så patchar
# träffar rätt `request`/`_check_api_key`.
from odoo.addons.ai_agent_core.controllers import stream as stream_mod


class _FakeHttpRequest:
    """Minsta möjliga httprequest-stub (headers läses av _check_api_key)."""

    def __init__(self, headers=None):
        self.headers = headers or {}
        self.data = b''


class _FakeRequest:
    """Stub för odoo.http.request — exponerar env/user/update_env."""

    def __init__(self, env):
        self.env = env
        self.httprequest = _FakeHttpRequest()
        self.uid = env.uid
        self.user = env.user

    def update_env(self, user=None, context=None, **kwargs):
        if user is not None:
            self.env = self.env(user=user)
            self.uid = self.env.uid
            self.user = self.env.user
        return self.env


@tagged('post_install')
class TestToolsRunCoworkerContext(common.TransactionCase):
    """tools_run(): rätt coworker-kontext + tydliga felmeddelanden."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Session = cls.env['ai.coworker.session']
        cls.Coworker = cls.env['ai.coworker']
        cls.Tool = cls.env['ai.tool']
        cls.user = cls.env['res.users'].create({
            'name': 'Tools Run User',
            'login': 'tools_run@example.com',
            'email': 'tools_run@example.com',
        })
        cls.coworker_a = cls.Coworker.create({
            'name': 'Tools Run A',
            'description': 'T/11533 coworker A',
            'status': 'active',
        })
        cls.coworker_b = cls.Coworker.create({
            'name': 'Tools Run B',
            'description': 'T/11533 coworker B',
            'status': 'active',
        })
        # Custom-verktyg med egen (sandboxade) kod — ingen extern beroende.
        cls.tool_a = cls.Tool.create({
            'name': 't11533_tool_a',
            'description': 'T/11533 test tool A',
            'code': "async def execute(**kwargs):\n    return 'tool-a-ok'",
            'risk_level': 'read_only',
            'executor': 'local',
        })
        cls.tool_b = cls.Tool.create({
            'name': 't11533_tool_b',
            'description': 'T/11533 test tool B',
            'code': "async def execute(**kwargs):\n    return 'tool-b-ok'",
            'risk_level': 'read_only',
            'executor': 'local',
        })
        cls.coworker_a.write({'tool_ids': [(6, 0, [cls.tool_a.id])]})
        cls.coworker_b.write({'tool_ids': [(6, 0, [cls.tool_b.id])]})

    def _call_tools_run(self, body):
        """Anropa tools_run() med fejkad request (auth patchad).

        tools_run() läser body ur request.httprequest.data (inte kw), så
        JSON:en läggs på stubben — samma väg som en riktig POST.
        """
        fake = _FakeRequest(self.env(user=self.user))
        fake.httprequest.data = json.dumps(body).encode('utf-8')
        with patch.object(stream_mod, 'request', fake), \
             patch.object(stream_mod.AIOpenAIAPI, '_check_api_key',
                          return_value=self.user):
            resp = stream_mod.AIOpenAIAPI().tools_run()
        return json.loads(resp.get_data(as_text=True))

    def _tool_call(self, name):
        return {'id': 'call_1', 'name': name, 'arguments': {}}

    # ── (a) coworker + pi_session_id → verktyget körs ──────────────────

    def test_coworker_with_pi_session_runs_tool(self):
        """Explicit coworker + pi_session_id → verktyget körs."""
        pid = 'aaaa1111-2222-3333-4444-555555555555'
        payload = self._call_tools_run({
            'coworker': self.coworker_a.name,
            'pi_session_id': pid,
            'tool_calls': [self._tool_call('t11533_tool_a')],
        })
        results = payload.get('results') or []
        self.assertEqual(len(results), 1, payload)
        self.assertFalse(results[0]['is_error'], results[0])
        self.assertIn('tool-a-ok', results[0]['result'])

    # ── (b) coworker utan session, senaste session = annan coworker ────

    def test_coworker_wins_over_stale_other_coworker_session(self):
        """Steg 1-skydd: annan aktiv session får inte styra verktygsregistret."""
        # Användarens senaste aktiva session tillhör coworker B.
        self.Session.create({
            'coworker_id': self.coworker_b.id,
            'status': 'active',
            'user_id': self.user.id,
            'name': 'stale B session',
        })
        # Anropet pekar på coworker A och skickar varken session_id eller
        # pi_session_id → fallbacken (senaste aktiva) skulle ge B:s register.
        payload = self._call_tools_run({
            'coworker': self.coworker_a.name,
            'tool_calls': [self._tool_call('t11533_tool_a')],
        })
        results = payload.get('results') or []
        self.assertEqual(len(results), 1, payload)
        self.assertFalse(
            results[0]['is_error'],
            'A:s verktyg ska köras trots att senaste sessionen är B:s: %s'
            % results[0])

    # ── (c) okänt verktyg → "Unknown tool" ─────────────────────────────

    def test_unknown_tool_message(self):
        """Namn som inte finns i ai.tool → 'Unknown tool'."""
        payload = self._call_tools_run({
            'coworker': self.coworker_a.name,
            'tool_calls': [self._tool_call('t11533_no_such_tool')],
        })
        results = payload.get('results') or []
        self.assertEqual(len(results), 1, payload)
        self.assertTrue(results[0]['is_error'])
        self.assertIn("Unknown tool 't11533_no_such_tool'",
                      results[0]['result'])

    # ── (d) verktyg finns men annan coworker → kontextmeddelande ───────

    def test_tool_exists_but_wrong_coworker_message(self):
        """Verktyg i ai.tool men ej i coworkerns kontext → specifikt meddelande."""
        payload = self._call_tools_run({
            'coworker': self.coworker_a.name,
            'tool_calls': [self._tool_call('t11533_tool_b')],
        })
        results = payload.get('results') or []
        self.assertEqual(len(results), 1, payload)
        self.assertTrue(results[0]['is_error'])
        self.assertIn('not available for coworker', results[0]['result'])
        self.assertIn(self.coworker_a.name, results[0]['result'])
        self.assertNotIn('Unknown tool', results[0]['result'])
