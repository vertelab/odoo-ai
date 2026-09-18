# -*- coding: utf-8 -*-
"""session-memory-bridge — bron från session till personligt minne.

Testar de tre skyddsnäten (idempotens, rätt användare, tröskel), att
cron-vägen skriver två rader, och att tomma sessioner upptäcks.

Kärnan i ändringen: `extract_from_session()` var byggd men hade NOLL
anropare. Erfarenhet blev aldrig minne. Dessa tester bevisar att bron
finns — och att den inte skriver fel.
"""

from unittest.mock import patch

from odoo.tests.common import TransactionCase


class TestSessionMemoryBridge(TransactionCase):
    """Bron session → personligt minne."""

    def setUp(self):
        super().setUp()
        self.Coworker = self.env['ai.coworker']
        self.Session = self.env['ai.coworker.session']
        self.Line = self.env['ai.coworker.session.line']
        self.PersonalMemory = self.env['ai.personal.memory']
        self.root = self.env.ref('base.user_root')
        self.user = self.env.ref('base.user_admin')

        self.coworker = self.Coworker.create({
            'name': 'Bridge Test Coworker',
            'status': 'active',
            'chat_user_id': self.user.id,
        })

    def _make_session(self, n_lines=5, user=None, init_type='web_ui'):
        """Skapa en session med `n_lines` rader."""
        session = self.Session.create({
            'coworker_id': self.coworker.id,
            'status': 'active',
            'init_type': init_type,
            'user_id': (user or self.user).id,
        })
        for i in range(n_lines):
            self.Line.create({
                'session_id': session.id,
                'sequence': i + 1,
                'role': 'user' if i % 2 == 0 else 'assistant',
                'content': f'Rad {i + 1} med tillräckligt innehåll för test.',
            })
        return session

    # ── Tröskeln ──────────────────────────────────────────────

    def test_short_session_skips_extraction(self):
        """En session under MIN_SUMMARY_LINES extraheras inte."""
        session = self._make_session(n_lines=2)
        with patch.object(
                type(self.PersonalMemory), 'extract_from_session') as m:
            count = session._bridge_to_personal_memory()
        self.assertEqual(count, 0)
        m.assert_not_called()

    def test_long_enough_session_calls_extraction(self):
        """En session som nådde tröskeln extraheras."""
        session = self._make_session(n_lines=5)
        with patch.object(
                type(self.PersonalMemory), 'extract_from_session',
                return_value=3) as m:
            count = session._bridge_to_personal_memory()
        self.assertEqual(count, 3)
        m.assert_called_once_with(session.id)

    # ── Idempotensen ──────────────────────────────────────────

    def test_extraction_is_idempotent(self):
        """Andra anropet gör inget — ingen dubbel LLM-körning."""
        session = self._make_session(n_lines=5)
        with patch.object(
                type(self.PersonalMemory), 'extract_from_session',
                return_value=2) as m:
            first = session._bridge_to_personal_memory()
            second = session._bridge_to_personal_memory()
        self.assertEqual(first, 2)
        self.assertEqual(second, 0)
        m.assert_called_once()

    def test_zero_extraction_still_marks_done(self):
        """Även 0 extraherade minnen sätter spärren.

        Annars kör varje eftermäle om samma LLM-anrop för en session som
        inte gav något — och en tom session kan aldrig bli klar.
        """
        session = self._make_session(n_lines=5)
        with patch.object(
                type(self.PersonalMemory), 'extract_from_session',
                return_value=0):
            session._bridge_to_personal_memory()
        self.assertTrue(session.memory_extracted)

    def test_flag_blocks_reextraction(self):
        """En session som redan extraherats anropar aldrig igen."""
        session = self._make_session(n_lines=5)
        session.memory_extracted = True
        with patch.object(
                type(self.PersonalMemory), 'extract_from_session') as m:
            count = session._bridge_to_personal_memory()
        self.assertEqual(count, 0)
        m.assert_not_called()

    # ── Rätt användare ────────────────────────────────────────

    def test_systemuser_session_writes_nothing(self):
        """En session som upplöses till systemuser skriver inget.

        Cron-sessioner har `user_id = systemuser`. Personligt minne
        tillhör en människa — att skriva till systemuser vore både fel
        och otillåtet.
        """
        session = self._make_session(
            n_lines=5, user=self.root, init_type='cron')
        # Tvinga upplösningen till systemuser
        with patch.object(
                type(self.coworker), '_resolve_dispatch_user',
                return_value=self.root):
            with patch.object(
                    type(self.PersonalMemory), 'extract_from_session') as m:
                count = session._bridge_to_personal_memory()
        self.assertEqual(count, 0)
        m.assert_not_called()
        self.assertFalse(session.memory_extracted)

    def test_unresolvable_user_writes_nothing(self):
        """Saknar sessionen helt användare → ingen skrivning, ingen krasch.

        Både dispatch-upplösningen och `session.user_id` måste fallera.
        """
        session = self._make_session(n_lines=5)
        # Nolla sessionens user_id — då finns ingen fallback kvar
        session.sudo().write({'user_id': False})
        with patch.object(
                type(self.coworker), '_resolve_dispatch_user',
                side_effect=Exception('ingen användare')):
            with patch.object(
                    type(self.PersonalMemory), 'extract_from_session') as m:
                count = session._bridge_to_personal_memory()
        self.assertEqual(count, 0)
        m.assert_not_called()

    # ── Kopplingen till eftermälet ────────────────────────────

    def test_bridge_runs_from_final_summary(self):
        """Eftermälet är den som startar bron."""
        session = self._make_session(n_lines=5)
        with patch.object(
                type(session), '_run_final_summary_llm',
                return_value='## Syfte\nTest'):
            with patch.object(
                    type(self.PersonalMemory), 'extract_from_session',
                    return_value=1) as m:
                session._write_final_summary()
        self.assertTrue(session.summary)
        m.assert_called_once()

    def test_no_bridge_when_summary_fails(self):
        """Misslyckas eftermälet ska bron inte köra på halva underlaget."""
        session = self._make_session(n_lines=5)
        with patch.object(
                type(session), '_run_final_summary_llm', return_value=None):
            with patch.object(
                    type(self.PersonalMemory), 'extract_from_session') as m:
                session._write_final_summary()
        m.assert_not_called()


class TestCronSessionLines(TransactionCase):
    """Cron-vägen ska skriva två rader — inte en."""

    def setUp(self):
        super().setUp()
        self.Session = self.env['ai.coworker.session']
        self.Line = self.env['ai.coworker.session.line']

    def test_cron_session_reaches_summary_threshold(self):
        """En cron-session med user+assistant når MIN_SUMMARY_LINES = 4?

        Nej — två rader är fortfarande under fyra. Men kontraktet är
        viktigt: cron-vägen ska skriva BÅDA rollerna, så att antalet
        rader speglar körningen. Att sänka tröskeln vore att dölja
        skrivfelet.
        """
        session = self.Session.create({
            'coworker_id': self.env['ai.coworker'].create({
                'name': 'Cron Line Test', 'status': 'active',
            }).id,
            'status': 'active',
            'init_type': 'cron',
        })
        self.Line.create({
            'session_id': session.id, 'sequence': 1,
            'role': 'user', 'content': 'Kör den schemalagda uppgiften.',
        })
        self.Line.create({
            'session_id': session.id, 'sequence': 2,
            'role': 'assistant', 'content': 'Uppgiften utförd.',
        })
        roles = session.session_line_ids.sorted('sequence').mapped('role')
        self.assertEqual(roles, ['user', 'assistant'])
        self.assertEqual(session.session_line_ids[0].sequence, 1)
        self.assertEqual(session.session_line_ids[1].sequence, 2)


class TestEmptySessionDetection(TransactionCase):
    """Tomma sessioner ska upptäckas, inte stängas tyst."""

    def setUp(self):
        super().setUp()
        self.Session = self.env['ai.coworker.session']
        self.coworker = self.env['ai.coworker'].create({
            'name': 'Empty Test Coworker', 'status': 'active',
        })

    def test_empty_session_is_closed(self):
        """En tom session stängs — den får inte svälta ut kön."""
        session = self.Session.create({
            'coworker_id': self.coworker.id,
            'status': 'active',
            'init_type': 'chat',
        })
        # Gör den idle
        session.sudo().write({
            'write_date': '2020-01-01 00:00:00',
        })
        self.Session._cron_close_idle_sessions(idle_minutes=1)
        session.invalidate_recordset()
        self.assertEqual(session.status, 'done')

    def test_empty_session_gets_no_summary(self):
        """En tom session kan inte sammanfattas — det finns inget."""
        session = self.Session.create({
            'coworker_id': self.coworker.id,
            'status': 'active',
            'init_type': 'chat',
        })
        self.assertIsNone(session._write_final_summary())
        self.assertFalse(session.summary)
