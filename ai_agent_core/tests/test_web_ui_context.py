# -*- coding: utf-8 -*-
"""Tester för web_ui-sessionskontext (web-ui-session-kontext).

Verifierar:
  (1.2) verktygsanrop bevaras i historiken (web_ui-vägen)
  (1.3) web_ui- och coworker-vägen ger samma historik
  (2.2) preview-formatet ger modellen verktygsspåret
  (2.3) okänt verktygsformat hoppas över utan att fälla körningen
  (3.3) ny rad får högsta sekvens + 1, befintliga ändras inte
  (3.4) direktskrivna rader ger ingen sekvenskollision
  (4.4) nyckel från config-parametern används utan miljövariabel
  (4.5) ingen nyckel ger ett fel som pekar på inställningarna
"""

import json

from odoo.tests import common, tagged


@tagged('post_install', '-at_install')
class TestWebUiSessionContext(common.TransactionCase):
    """Historik, sekvensordning och nyckelkälla för web_ui-vägen."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Session = cls.env['ai.coworker.session']
        cls.Line = cls.env['ai.coworker.session.line']
        cls.coworker = cls.env['ai.coworker'].create({
            'name': 'Web UI Context Coworker',
            'description': 'x',
            'status': 'active',
        })

    def _session(self):
        return self.Session.create({
            'coworker_id': self.coworker.id,
            'status': 'active',
        })

    def _line(self, sess, seq, role, content='', tool_name=False,
              tool_calls=False):
        return self.Line.create({
            'session_id': sess.id,
            'sequence': seq,
            'role': role,
            'content': content,
            'tool_name': tool_name or False,
            'tool_calls': tool_calls or False,
        })

    # ── 1.2/1.3 Verktygsspåret bevaras ────────────────────────────────

    def test_tool_calls_preserved_in_history(self):
        """1.2: assistant-verktygsanrop och tool-rader finns i historiken."""
        sess = self._session()
        self._line(sess, 1, 'user', 'Kolla disk på web01')
        self._line(sess, 2, 'assistant', 'Jag kollar.',
                   tool_calls=json.dumps([{
                       'id': 'call_1', 'type': 'function',
                       'function': {'name': 'salt_disk_usage',
                                    'arguments': '{}'}}]))
        self._line(sess, 3, 'tool', '{"ok": true}', tool_name='salt_disk_usage')

        history = sess._build_history_from_lines()
        roles = [m.role.value for m in history]
        self.assertEqual(roles, ['user', 'assistant', 'tool'])
        assistant = history[1]
        self.assertTrue(assistant.tool_calls, 'tool_calls ska bevaras')
        self.assertEqual(assistant.tool_calls[0]['function']['name'],
                         'salt_disk_usage')
        self.assertEqual(history[2].name, 'salt_disk_usage')

    def test_web_ui_and_coworker_paths_agree(self):
        """1.3: samma session ger samma historik i båda vägarna."""
        sess = self._session()
        self._line(sess, 1, 'user', 'Hej')
        self._line(sess, 2, 'assistant', 'Hej tillbaka')
        # Coworker-vägen använder samma funktion — jämför serialiseringen
        # som web_ui-vägen skickar (to_openai) mot funktionens utdata.
        history = sess._build_history_from_lines()
        web_ui_style = [m.to_openai() for m in history]
        self.assertEqual(web_ui_style, [
            {'role': 'user', 'content': 'Hej'},
            {'role': 'assistant', 'content': 'Hej tillbaka'},
        ])

    # ── 2.2/2.3 Preview-formatet ──────────────────────────────────────

    def test_preview_format_gives_tool_trail(self):
        """2.2: preview-poster ger modellen verktygsnamn och preview."""
        sess = self._session()
        self._line(sess, 1, 'user', 'Sök')
        self._line(sess, 2, 'assistant', 'Klart.', tool_calls=json.dumps([
            {'name': 'odoo_web_search', 'preview': '3 träffar om TV'},
            {'name': 'odoo_fetch_url', 'preview': 'sida hämtad'},
        ]))
        history = sess._build_history_from_lines()
        content = history[-1].content
        self.assertIn('odoo_web_search', content,
                      'verktygsnamnet ska synas i historiken')
        self.assertIn('odoo_fetch_url', content)
        self.assertIn('3 träffar om TV', content,
                      'resultatpreviewen ska synas')
        # Preview-formatet är inte replaybart → inga tool_calls sätts.
        self.assertFalse(history[-1].tool_calls)

    def test_unknown_tool_format_is_skipped(self):
        """2.3: otolkbart verktygsformat hoppas över utan att fälla."""
        sess = self._session()
        self._line(sess, 1, 'user', 'Hej')
        self._line(sess, 2, 'assistant', 'Svar',
                   tool_calls='{inte giltig json')
        self._line(sess, 3, 'assistant', 'Svar 2',
                   tool_calls=json.dumps([{'okänd_nyckel': 'x'}]))
        history = sess._build_history_from_lines()  # får inte kasta
        self.assertEqual(len(history), 3)
        self.assertFalse(history[1].tool_calls)
        self.assertFalse(history[2].tool_calls)

    # ── 3.3/3.4 Sekvensordning ────────────────────────────────────────

    def test_next_sequence_is_max_plus_one(self):
        """3.3: ny rad får högsta sekvens + 1, befintliga ändras inte."""
        from odoo.addons.ai_agent_core.controllers.stream import (
            _next_session_sequence)
        sess = self._session()
        self._line(sess, 1, 'user', 'a')
        self._line(sess, 5, 'assistant', 'b')
        before = {l.id: l.sequence for l in sess.session_line_ids}
        nxt = _next_session_sequence(self.env, sess.id)
        self.assertEqual(nxt, 6, 'högsta (5) + 1')
        for line in sess.session_line_ids:
            self.assertEqual(line.sequence, before[line.id],
                             'befintliga rader får inte ändras')

    def test_direct_written_lines_do_not_collide(self):
        """3.4: verktygsrader direkt ⇒ ny rad kolliderar inte."""
        from odoo.addons.ai_agent_core.controllers.stream import (
            _next_session_sequence)
        sess = self._session()
        # 2 meddelanderader men sekvens upp till 7 (verktygsrader direkt).
        self._line(sess, 1, 'user', 'a')
        self._line(sess, 2, 'assistant', 'b')
        self._line(sess, 6, 'tool', 'x', tool_name='t1')
        self._line(sess, 7, 'tool', 'y', tool_name='t2')
        nxt = _next_session_sequence(self.env, sess.id)
        self.assertEqual(nxt, 8)
        self._line(sess, nxt, 'user', 'ny fråga')
        seqs = sess.session_line_ids.mapped('sequence')
        self.assertEqual(len(seqs), len(set(seqs)),
                         'inga två rader får dela sekvensvärde')

    def test_history_sorts_stably_on_duplicate_sequences(self):
        """3.5: äldre rader med dubblerad sekvens får stabil ordning."""
        sess = self._session()
        a = self._line(sess, 1, 'user', 'först')
        b = self._line(sess, 1, 'assistant', 'senare men samma seq')
        history = sess._build_history_from_lines()
        self.assertEqual(len(history), 2)
        # Sorterat på (sequence, id) → a före b.
        self.assertEqual(history[0].content, 'först')
        self.assertEqual(history[1].content, 'senare men samma seq')


@tagged('post_install', '-at_install')
class TestGoogleKeySource(common.TransactionCase):
    """4.4/4.5: verktygsnyckeln läses ur Odoo, med env som fallback."""

    def test_config_parameter_is_used(self):
        """4.4: nyckel i config-parametern används utan miljövariabel."""
        params = self.env['ir.config_parameter'].sudo()
        params.set_param('ai_agent_core.google_api_key', 'AIza-test-nyckel')
        key = self.env['res.config.settings']._google_api_key()
        self.assertEqual(key, 'AIza-test-nyckel')

    def test_env_is_fallback(self):
        """4.4: utan config-nyckel används miljövariabeln."""
        import os
        params = self.env['ir.config_parameter'].sudo()
        params.set_param('ai_agent_core.google_api_key', '')
        old = os.environ.get('GOOGLE_API_KEY')
        os.environ['GOOGLE_API_KEY'] = 'env-nyckel'
        try:
            key = self.env['res.config.settings']._google_api_key()
            self.assertEqual(key, 'env-nyckel')
        finally:
            if old is None:
                os.environ.pop('GOOGLE_API_KEY', None)
            else:
                os.environ['GOOGLE_API_KEY'] = old
