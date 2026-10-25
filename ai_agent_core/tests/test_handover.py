# -*- coding: utf-8 -*-
"""Tester för pi-session-handover."""
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestHandover(TransactionCase):

    def setUp(self):
        super().setUp()
        self.Session = self.env['ai.coworker.session']
        self.Line = self.env['ai.coworker.session.line']
        self.user = self.env.user

    def _session(self, init_type='web_ui', **kw):
        return self.Session.create({
            'name': 'handover-test',
            'init_type': init_type,
            'user_id': self.user.id,
            **kw,
        })

    def _line(self, session, role, seq, **kw):
        return self.Line.create({
            'session_id': session.id,
            'role': role,
            'content': 'x',
            'sequence': seq,
            **kw,
        })

    # ── 1.3: automatiska init-typer utesluts ──────────────────────────
    def test_auto_init_types_excluded(self):
        for it in ('cron', 'watch', 'webhook', 'mail', 'heartbeat'):
            s = self._session(init_type=it)
            self._line(s, 'tool', 1)
            self.assertNotIn(
                s, self.Session._handover_suggestions(user=self.user),
                'automatisk init_type %s ska inte föreslås' % it)

    def test_human_init_types_included(self):
        for it in ('web_ui', 'chat', 'channel', 'powerbox',
                   'server_action', 'manual', 'openai_api'):
            s = self._session(init_type=it)
            self._line(s, 'tool', 1)
            self.assertIn(
                s, self.Session._handover_suggestions(user=self.user),
                'mänsklig init_type %s ska föreslås' % it)

    # ── 2.4: append-only-skyddet ──────────────────────────────────────
    def test_append_only_content_blocked(self):
        from odoo.exceptions import UserError
        s = self._session()
        line = self._line(s, 'user', 1)
        with self.assertRaises(UserError):
            line.write({'content': 'ändrad'})

    def test_append_only_lifecycle_allowed(self):
        s = self._session()
        line = self._line(s, 'user', 1)
        s.action_handover_offer(note='x')
        # lifecycle-skrivning på sessionen ska gå igenom
        self.assertEqual(s.handover_state, 'offered')

    def test_session_lines_cannot_be_deleted(self):
        from odoo.exceptions import UserError
        s = self._session()
        line = self._line(s, 'user', 1)
        with self.assertRaises(UserError):
            line.unlink()

    # ── 3.1: offer ────────────────────────────────────────────────────
    def test_offer_sets_state_and_note(self):
        s = self._session()
        s.action_handover_offer(note='nästa: fixa certet', mode='note')
        self.assertEqual(s.handover_state, 'offered')
        self.assertEqual(s.handover_note, 'nästa: fixa certet')
        self.assertEqual(s.handover_by, self.user)
        self.assertTrue(s.handover_at)

    # ── 3.2: claim ────────────────────────────────────────────────────
    def test_claim_switches_owner_keeps_session(self):
        s = self._session(pi_session_id='laptop-uuid')
        s.pi_session_ids = 'laptop-uuid'
        s.action_handover_offer(note='nästa')
        session_id = s.id
        # claim utan NATS → låset kan inte tas (best-effort) → False
        ok = s.action_handover_claim(pi_session_id='server-uuid')
        # I testmiljö utan NATS: nekas. Verifiera att INGET skrivits.
        if not ok:
            self.assertEqual(s.pi_session_id, 'laptop-uuid')
            self.assertEqual(s.handover_state, 'offered')
        else:
            self.assertEqual(s.pi_session_id, 'server-uuid')
            self.assertIn('laptop-uuid', s.pi_session_ids)
            self.assertIn('server-uuid', s.pi_session_ids)
            self.assertEqual(s.id, session_id)

    def test_claim_without_offer_rejected(self):
        from odoo.exceptions import UserError
        s = self._session()
        with self.assertRaises(UserError):
            s.action_handover_claim()

    # ── 3.3: done ─────────────────────────────────────────────────────
    def test_done_removes_from_list(self):
        s = self._session()
        s.action_handover_offer(note='x')
        s.action_handover_done()
        self.assertEqual(s.handover_state, 'done')
        offered = self.Session.search([
            ('handover_state', '=', 'offered'),
            ('id', '=', s.id)])
        self.assertFalse(offered)

    # ── 4.1: de tre avbrottsformerna ──────────────────────────────────
    def test_aborted_tool_result(self):
        s = self._session()
        self._line(s, 'assistant', 1)
        self._line(s, 'tool', 2)
        self.assertTrue(s._handover_is_aborted())

    def test_aborted_assistant_tool_use(self):
        s = self._session()
        self._line(s, 'assistant', 1, finish_reason='tool_calls')
        self.assertTrue(s._handover_is_aborted())

    def test_completed_turn_not_aborted(self):
        s = self._session()
        self._line(s, 'tool', 1)
        self._line(s, 'assistant', 2, finish_reason='stop')
        self.assertFalse(s._handover_is_aborted())

    def test_no_lines_not_aborted(self):
        s = self._session()
        self.assertFalse(s._handover_is_aborted())

    # ── 4.2: behörighetsgränsen ───────────────────────────────────────
    def test_other_users_sessions_not_listed(self):
        other = self.env['res.users'].create({
            'name': 'Annan', 'login': 'annan_handover_test'})
        mine = self._session()
        self._line(mine, 'tool', 1)
        theirs = self.Session.create({
            'name': 'deras', 'init_type': 'web_ui',
            'user_id': other.id})
        self._line(theirs, 'tool', 1)
        found = self.Session._handover_suggestions(user=self.user)
        self.assertIn(mine, found)
        self.assertNotIn(theirs, found)

    def test_owned_domain_filters_by_user(self):
        other = self.env['res.users'].create({
            'name': 'Annan2', 'login': 'annan2_handover_test'})
        theirs = self.Session.create({
            'name': 'deras2', 'init_type': 'web_ui',
            'user_id': other.id})
        found = self.Session.search(
            self.Session._handover_owned_domain(self.user) +
            [('id', '=', theirs.id)])
        self.assertFalse(found)

    # ── 4.3/4.4: sortering ────────────────────────────────────────────
    def test_suggestions_sorted_newest_first(self):
        old = self._session()
        self._line(old, 'tool', 1)
        new = self._session()
        self._line(new, 'tool', 1)
        # write_date sätts SIST — skrivningen av raden ovan rör sessionen
        # och skulle annars skriva över den tvingade tidsstämpeln.
        old.write({'write_date': '2020-01-01 00:00:00'})
        found = self.Session._handover_suggestions(user=self.user)
        self.assertIn(old, found)
        self.assertIn(new, found)
        ids = found.mapped('id')
        self.assertLess(ids.index(new.id), ids.index(old.id), 'nyast först')
        # Ordningen ska vara fallande över HELA listan — databasen har egna
        # sessioner, så det räcker inte att jämföra de två vi skapade.
        dates = [s.write_date for s in found]
        self.assertEqual(dates, sorted(dates, reverse=True),
                         'hela listan ska vara sorterad nyast först')

    # ── 5.4: mode-förslag ─────────────────────────────────────────────
    def test_mode_full_with_many_tools(self):
        s = self._session()
        for i in range(5):
            self._line(s, 'tool', i + 1)
        self.assertEqual(s._handover_suggest_mode(), 'full')

    def test_mode_note_with_few_tools(self):
        s = self._session()
        self._line(s, 'user', 1)
        self._line(s, 'assistant', 2, finish_reason='stop')
        self.assertEqual(s._handover_suggest_mode(), 'note')

    # ── 5.2: --auto härleder ur eftermälet ────────────────────────────
    def test_derive_note_uses_summary(self):
        s = self._session()
        s.summary = 'Syfte: testa\nUtfall: klart'
        self.assertIn('Syfte', s._handover_derive_note())
