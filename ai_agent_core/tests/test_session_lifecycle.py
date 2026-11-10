# -*- coding: utf-8 -*-
"""Sessionens livscykel (session-observability).

Bakgrunden: `mark_done()` anropas bara från webhook-vägen och
`mark_interrupted()` aldrig i drift. `_cron_close_idle_sessions` stänger
`status='active'`-sessioner — men `_find_or_create_coworker_session` hittar
dem på `pi_session_id` och skriver nya turer UTAN att röra status. En session
som en gång stängts låg därför kvar som `done` med en `end_date` som inte
längre stämde, och cronen hittade den aldrig igen.

Dessa tester låser att:

  1. En klientstängning blir en `close_event`-rad + `status='done'`.
  2. Upprepad stängning (samma reason + at) inte dubblerar.
  3. En stängd session ÅTERÖPPNAS när en ny tur kommer.
  4. Idle-cronen skriver `close_event` och fångar sessionen igen efter
     återöppning.
"""

import uuid

from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestSessionLifecycle(TransactionCase):
    """Stängning som händelse, och återöppning."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Session = cls.env['ai.coworker.session']
        cls.CloseEvent = cls.env['ai.coworker.session.close_event']
        cls.user = cls.env['res.users'].create({
            'name': 'Lifecycle User',
            'login': 'lifecycle@example.com',
            'email': 'lifecycle@example.com',
        })
        cls.coworker = cls.env['ai.coworker'].create({
            'name': 'Lifecycle Coworker',
            'description': 'lifecycle test',
            'status': 'active',
        })

    def _session(self, **vals):
        base = {
            'coworker_id': self.coworker.id,
            'user_id': self.user.id,
            'status': 'active',
            'init_type': 'openai_api',
            'pi_session_id': str(uuid.uuid4()),
        }
        base.update(vals)
        return self.Session.create(base)

    def _age(self, session, when='2020-01-01 00:00:00'):
        """Sätt `write_date` bakåt i tiden (ORM:en skriver över det).

        `write_date` är ett magic-fält som Odoo sätter vid varje write, så
        `session.write({'write_date': ...})` ignoreras. Cronens domän läser
        kolumnen direkt, så testet måste göra det också.
        """
        # Flusha först: annars skriver Odoos flush över `write_date` med
        # "nu" när nästa search triggar en pending write (klassisk ORM-fälla
        # när man rör magic-fält med rå SQL).
        self.env.flush_all()
        self.env.cr.execute(
            'UPDATE ai_coworker_session SET write_date = %s WHERE id = %s',
            (when, session.id))
        self.env.invalidate_all()

    # ── 1. Klientstängning ─────────────────────────────────────────────

    def test_close_from_client_records_event_and_status(self):
        ses = self._session()
        n = ses._close_from_client(reason='quit')
        self.assertEqual(n, 1)
        self.assertEqual(ses.status, 'done')
        self.assertEqual(ses.finish_reason, 'closed')
        self.assertTrue(ses.end_date)
        events = self.CloseEvent.search([('session_id', '=', ses.id)])
        self.assertEqual(len(events), 1)
        self.assertEqual(events.reason, 'quit')
        self.assertEqual(events.pi_session_id, ses.pi_session_id)

    def test_close_reason_maps_to_finish_reason(self):
        """`new`/`fork`/`resume` betyder sessionsbyte, inte normalt avslut."""
        ses = self._session()
        ses._close_from_client(reason='new')
        self.assertEqual(ses.finish_reason, 'new_session')

    def test_close_uses_client_timestamp(self):
        """Tidsstämpeln är klientens — inte när Odoo tog emot anropet."""
        from datetime import datetime
        at = datetime(2026, 11, 9, 8, 30, 0)
        ses = self._session()
        ses._close_from_client(reason='quit', at=at)
        event = self.CloseEvent.search([('session_id', '=', ses.id)])
        self.assertEqual(event.at, at)
        self.assertEqual(ses.end_date, at)

    # ── 2. Idempotens ──────────────────────────────────────────────────

    def test_close_is_idempotent(self):
        from datetime import datetime
        at = datetime(2026, 11, 9, 9, 0, 0)
        ses = self._session()
        self.assertEqual(ses._close_from_client(reason='quit', at=at), 1)
        self.assertEqual(ses._close_from_client(reason='quit', at=at), 0)
        events = self.CloseEvent.search([('session_id', '=', ses.id)])
        self.assertEqual(len(events), 1)

    def test_two_different_closes_both_persist(self):
        from datetime import datetime
        ses = self._session()
        ses._close_from_client(reason='quit',
                               at=datetime(2026, 11, 9, 9, 0, 0))
        ses._close_from_client(reason='new',
                               at=datetime(2026, 11, 9, 10, 0, 0))
        events = self.CloseEvent.search(
            [('session_id', '=', ses.id)], order='at')
        self.assertEqual([e.reason for e in events], ['quit', 'new'])
        # Första raden är oförändrad
        self.assertEqual(events[0].reason, 'quit')

    # ── 3. Återöppning ─────────────────────────────────────────────────

    def test_reopen_if_closed(self):
        ses = self._session()
        ses._close_from_client(reason='quit')
        self.assertEqual(ses.status, 'done')
        self.assertTrue(ses._reopen_if_closed(reason='resume'))
        self.assertEqual(ses.status, 'active')
        self.assertFalse(ses.end_date)
        # Stängningshistoriken rörs inte
        self.assertEqual(
            len(self.CloseEvent.search([('session_id', '=', ses.id)])), 1)

    def test_reopen_does_not_touch_error(self):
        """Ett fel ska inte maskeras av att användaren frågar igen."""
        ses = self._session(status='error')
        self.assertFalse(ses._reopen_if_closed())
        self.assertEqual(ses.status, 'error')

    def test_new_turn_reopens_closed_session(self):
        """En ny tur till en stängd session gör den aktiv igen."""
        ses = self._session()
        pid = ses.pi_session_id
        ses._close_from_client(reason='quit')
        found, created = self.Session._find_or_create_coworker_session(
            self.coworker.id, self.user.id, pi_session_id=pid)
        self.assertEqual(found.id, ses.id)
        self.assertFalse(created)
        self.assertEqual(found.status, 'active')
        self.assertFalse(found.end_date)

    def test_lookup_reopens_closed_session(self):
        ses = self._session()
        pid = ses.pi_session_id
        ses._close_from_client(reason='quit')
        found, created = self.Session._lookup_or_create_pi_session(pid)
        self.assertEqual(found.id, ses.id)
        self.assertFalse(created)
        self.assertEqual(found.status, 'active')

    # ── 4. Idle-cronen ─────────────────────────────────────────────────

    def test_idle_cron_writes_close_event(self):
        ses = self._session()
        # Gör sessionen "övergiven": write_date äldre än cutoff.
        self._age(ses)
        closed = self.Session._cron_close_idle_sessions(
            idle_minutes=1, extra_domain=[('id', '=', ses.id)])
        self.assertEqual(closed, 1)
        self.assertEqual(ses.status, 'done')
        self.assertEqual(ses.finish_reason, 'idle')
        events = self.CloseEvent.search([('session_id', '=', ses.id)])
        self.assertEqual(len(events), 1)
        self.assertEqual(events.reason, 'idle')

    def test_idle_cron_catches_reopened_session_again(self):
        """Efter återöppning fångar cronen sessionen på nytt."""
        ses = self._session()
        self._age(ses)
        self.Session._cron_close_idle_sessions(
            idle_minutes=1, extra_domain=[('id', '=', ses.id)])
        self.assertEqual(ses.status, 'done')
        # En ny tur återöppnar
        self.Session._find_or_create_coworker_session(
            self.coworker.id, self.user.id,
            pi_session_id=ses.pi_session_id)
        self.assertEqual(ses.status, 'active')
        # Åldra igen och kör cronen: den ska stängas på nytt.
        # ⚠ `close_event.at` har sekundupplösning: körs andra stängningen i
        # SAMMA sekund som den första är den en dubblett (dedupen är
        # avsiktlig). Flytta klockan en sekund så testet mäter "en ny
        # stängning", inte "två stängningar i samma sekund".
        self.env.cr.execute(
            "UPDATE ai_coworker_session_close_event "
            "SET at = at - interval '60 seconds' WHERE session_id = %s",
            (ses.id,))
        self.env.invalidate_all()
        self._age(ses)
        closed = self.Session._cron_close_idle_sessions(
            idle_minutes=1, extra_domain=[('id', '=', ses.id)])
        self.assertEqual(closed, 1)
        events = self.CloseEvent.search(
            [('session_id', '=', ses.id)], order='id')
        self.assertEqual(len(events), 2,
                         'andra stängningen ska bli en ny rad')
        self.assertEqual([e.reason for e in events], ['idle', 'idle'])
