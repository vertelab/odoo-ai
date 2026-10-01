# -*- coding: utf-8 -*-
"""Tester för session-history-relevance.

Verifierar:
  (1.1) web_ui-vägens historik är Message-objekt med verktygsspåret bevarat —
        ingen andra loop filtrerar bort tool-rader eller tool_calls.
  (1.2) historiken som hoistas till generatorn är identisk med
        `_build_history_from_lines()` (ingen ombyggnad tappar spåret).
  (1.3) acceptansfallet: en session med user → assistant(tool_calls) →
        tool → tool ger 4 meddelanden med verktygsspåret intakt.
"""

import json

from odoo.tests import common, tagged


@tagged('post_install', '-at_install')
class TestSessionHistoryRelevance(common.TransactionCase):
    """Web_ui-vägens historikbygge."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Session = cls.env['ai.coworker.session']
        cls.Line = cls.env['ai.coworker.session.line']
        cls.coworker = cls.env['ai.coworker'].create({
            'name': 'History Relevance Coworker',
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

    # ── 1.3 Acceptansfall: 4 meddelanden med verktygsspåret ───────────

    def test_four_messages_with_tool_trail_preserved(self):
        """1.3: user → assistant(tool_calls) → tool → tool = 4 meddelanden."""
        sess = self._session()
        self._line(sess, 1, 'user', 'Kolla disk')
        self._line(sess, 2, 'assistant', 'Jag kollar.',
                   tool_calls=json.dumps([{
                       'id': 'call_1', 'type': 'function',
                       'function': {'name': 'salt_disk_usage',
                                    'arguments': '{}'}}]))
        self._line(sess, 3, 'tool', '{"ok": true}',
                   tool_name='salt_disk_usage')
        self._line(sess, 4, 'tool', '{"ok": true}',
                   tool_name='salt_disk_usage')

        history = sess._build_history_from_lines()
        self.assertEqual(len(history), 4, 'fyra meddelanden ska bevaras')
        roles = [m.role.value for m in history]
        self.assertEqual(roles, ['user', 'assistant', 'tool', 'tool'])
        self.assertTrue(history[1].tool_calls,
                        'assistant-tool_calls ska bevaras')
        self.assertEqual(history[1].tool_calls[0]['function']['name'],
                         'salt_disk_usage')
        self.assertEqual(history[2].name, 'salt_disk_usage')
        self.assertEqual(history[3].name, 'salt_disk_usage')

    # ── 1.1/1.2 Ingen andra loop tappar spåret ────────────────────────

    def test_hoisted_history_is_message_objects_with_trail(self):
        """1.2: historiken som hoistas är Message-objekt, inte dictar.

        Web_ui-vägen ska behålla Message-objekten hela vägen — serialisering
        till dictar följt av en andra loop tappade verktygsspåret.
        """
        from odoo.addons.ai_agent_core.core.provider import Message
        sess = self._session()
        self._line(sess, 1, 'user', 'Hej')
        self._line(sess, 2, 'assistant', 'Svar',
                   tool_calls=json.dumps([{
                       'id': 'call_1', 'type': 'function',
                       'function': {'name': 'odoo_search',
                                    'arguments': '{}'}}]))
        self._line(sess, 3, 'tool', '{"ok": true}', tool_name='odoo_search')

        history = list(sess._build_history_from_lines())
        for m in history:
            self.assertIsInstance(
                m, Message,
                'historiken ska vara Message-objekt (ingen dict-omvandling)')
        # Verktygsspåret finns kvar — ingen loop filtrerar bort det.
        self.assertTrue(any(m.role.value == 'tool' for m in history),
                        'tool-rader ska finnas kvar i historiken')
        self.assertTrue(any(m.tool_calls for m in history),
                        'assistant-tool_calls ska finnas kvar')

    def test_preview_format_reaches_model(self):
        """1.1: preview-formatets verktygsspår filtreras inte bort."""
        sess = self._session()
        self._line(sess, 1, 'user', 'Nyheter')
        self._line(sess, 2, 'assistant', 'Klart.', tool_calls=json.dumps([
            {'name': 'personal_memory', 'preview': '{"written": true}'},
            {'name': 'news_digest', 'preview': '{"cached": false}'},
        ]))
        history = sess._build_history_from_lines()
        content = history[-1].content
        self.assertIn('personal_memory', content,
                      'verktygsnamnet ska nå modellen')
        self.assertIn('news_digest', content)
        self.assertIn('written', content,
                      'resultatpreviewen ska nå modellen')

    # ── 2.1/2.2 Relevansbedömning ──────────────────────────────────────

    def _multi_turn_session(self):
        """En session med flera turer om olika ämnen."""
        sess = self._session()
        # Tur 1 — disk
        self._line(sess, 1, 'user', 'Kolla diskutrymme på web01')
        self._line(sess, 2, 'assistant', 'Disken är full på web01.')
        # Tur 2 — nyheter
        self._line(sess, 3, 'user', 'Visa senaste nyheterna')
        self._line(sess, 4, 'assistant', 'Här är nyheterna.')
        # Tur 3 — fakturor (senaste)
        self._line(sess, 5, 'user', 'Skicka fakturan till kunden')
        self._line(sess, 6, 'assistant', 'Fakturan är skickad.')
        return sess

    def test_last_turn_always_kept(self):
        """2.1: senaste turen behålls alltid, även med liten budget."""
        sess = self._multi_turn_session()
        history = sess._build_history_from_lines()
        selected = sess._select_history_for_query(
            history, 'Skicka fakturan till kunden', keep_last=1, budget=2)
        contents = [m.content for m in selected]
        self.assertIn('Skicka fakturan till kunden', contents,
                      'senaste turens user-rad ska alltid ingå')
        self.assertIn('Fakturan är skickad.', contents,
                      'senaste turens assistant-rad ska alltid ingå')

    def test_relevant_older_turn_can_be_kept(self):
        """2.2: en relevant äldre tur kan ingå trots att den inte är sist."""
        sess = self._multi_turn_session()
        history = sess._build_history_from_lines()
        # Fråga om disk → tur 1 (disken) ska rankas in, nyheterna inte.
        selected = sess._select_history_for_query(
            history, 'Hur mycket diskutrymme finns kvar på web01?',
            keep_last=1, budget=6)
        contents = ' '.join(m.content for m in selected)
        self.assertIn('diskutrymme', contents,
                      'relevant äldre tur ska kunna ingå')
        self.assertIn('Skicka fakturan', contents,
                      'senaste turen ska ingå')

    def test_selection_stays_chronological(self):
        """2.2: urvalet behåller kronologisk ordning."""
        sess = self._multi_turn_session()
        history = sess._build_history_from_lines()
        selected = sess._select_history_for_query(
            history, 'disk web01', keep_last=1, budget=6)
        seqs = [m.content for m in selected]
        # Tur 1 före tur 3 i resultatet
        self.assertLess(seqs.index('Kolla diskutrymme på web01'),
                        seqs.index('Skicka fakturan till kunden'))
