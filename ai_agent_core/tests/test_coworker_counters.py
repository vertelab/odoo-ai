# -*- coding: utf-8 -*-
"""Formulärets räknare (coworker-form-performance F5).

VARFÖR: räknarna satt på olika perioder (Sessions = all-time, Tokens =
månad), en av dem räknade en modell som inte fanns (Objects = alltid 0), och
Odoo Mind räknade rader i stället för koncept (9 av 39 741 — 33 012 är
company-scope). Nu mäter de samma period och svarar på samma fråga som
listan de öppnar.

Dessa tester bevisar:
  1. Sessions räknar sessioner som STARTADES i månaden
  2. Tokens läser samma aggregering som budgeten
  3. Odoo Mind räknar distinkta konceptnycklar (fem versioner = 1)
  4. arkiverade och superseded-koncept räknas inte
  5. klicket öppnar exakt de rader räknaren räknade
"""

from datetime import date, timedelta

from odoo.tests.common import TransactionCase, tagged


@tagged('coworker_form_performance', 'post_install', '-at_install')
class TestCoworkerCounters(TransactionCase):
    """F5.1–F5.4: period, enhet och avgränsning."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Coworker = cls.env['ai.coworker']
        cls.Session = cls.env['ai.coworker.session']
        cls.Line = cls.env['ai.coworker.session.line']
        cls.Concept = cls.env['ai.okf.concept']
        cls.user = cls.env['res.users'].create({
            'name': 'Counters User',
            'login': 'counters@example.com',
            'email': 'counters@example.com',
        })
        cls.coworker = cls.Coworker.create({
            'name': 'Counters Coworker',
            'status': 'active',
        })
        cls.artifact = cls.env['ai.artifact.type'].search([], limit=1)

    # ── hjälpare ────────────────────────────────────────────────────────

    def _session(self, days_ago=0):
        session = self.Session.create({
            'coworker_id': self.coworker.id,
            'status': 'active',
            'user_id': self.user.id,
            'name': 'counters',
        })
        if days_ago:
            self.env.cr.execute(
                "UPDATE ai_coworker_session "
                "SET create_date = create_date - interval '%s days' "
                "WHERE id = %s", (days_ago, session.id))
            session.invalidate_recordset(['create_date'])
        return session

    def _concept(self, key, version=1, status='stable', archived=False,
                 supersedes=None):
        return self.Concept.create({
            'scope': 'coworker',
            'owner_coworker_id': self.coworker.id,
            'artifact_type_id': self.artifact.id,
            'concept_key': key,
            'version': version,
            'status': status,
            'archived': archived,
            'supersedes_id': supersedes.id if supersedes else False,
            'title': key,
            'summary': 'x',
        })

    # ── 1. Sessions mäter månaden ───────────────────────────────────────

    def test_sessions_counts_started_this_month(self):
        """F5.1: bara sessioner som STARTADE i månaden räknas."""
        self._session()                 # idag
        self._session(days_ago=1)       # i månaden (om vi inte är den 1:a)
        self._session(days_ago=400)     # förra året — räknas inte
        self.coworker.invalidate_recordset()

        month_start = date.today().replace(day=1)
        expected = self.Session.search_count([
            ('coworker_id', '=', self.coworker.id),
            ('create_date', '>=', month_start),
        ])
        self.assertEqual(self.coworker.session_count, expected)
        self.assertLess(self.coworker.session_count,
                        self.Session.search_count(
                            [('coworker_id', '=', self.coworker.id)]),
                        'all-time räknas inte längre')

    def test_sessions_action_opens_the_same_rows(self):
        """F5.1: klicket öppnar exakt de sessioner räknaren räknade."""
        self._session()
        self._session(days_ago=400)
        self.coworker.invalidate_recordset()
        action = self.coworker.action_get_sessions()
        self.assertEqual(
            self.Session.search_count(action['domain']),
            self.coworker.session_count)

    def test_session_across_month_boundary_counts_once(self):
        """Sessions-specen: en session som startade förra månaden räknas där."""
        session = self._session()
        self.env.cr.execute(
            "UPDATE ai_coworker_session SET create_date = "
            "date_trunc('month', now()) - interval '1 day' WHERE id = %s",
            (session.id,))
        session.invalidate_recordset(['create_date'])
        self.coworker.invalidate_recordset()
        self.assertEqual(self.coworker.session_count, 0)

    # ── 2. Tokens läser samma aggregering ──────────────────────────────

    def test_tokens_matches_month_sum(self):
        """F5.2: Tokens = månadssumman ur `_token_totals`."""
        session = self._session()
        self.Line.create({
            'session_id': session.id, 'role': 'assistant', 'content': 'x',
            'token_input': 1_500, 'sys_multiplier': 1.0})
        self.Line.create({
            'session_id': session.id, 'role': 'assistant', 'content': 'x',
            'token_input': 9_999, 'sys_multiplier': 1.0,
            'informative': True})
        self.coworker.invalidate_recordset()
        self.assertEqual(self.coworker.session_line_count, 1_500)

    def test_tokens_action_opens_the_same_rows(self):
        """F5.2: listan bakom Tokens filtrerar på samma period och flagga."""
        session = self._session()
        self.Line.create({
            'session_id': session.id, 'role': 'assistant', 'content': 'x',
            'token_input': 100, 'sys_multiplier': 1.0})
        action = self.coworker.action_get_session_lines()
        self.assertIn(('coworker_id', '=', self.coworker.id),
                      action['domain'])
        self.assertIn(('informative', '=', False), action['domain'])
        self.assertEqual(self.Line.search_count(action['domain']), 1)

    # ── 3. Odoo Mind räknar distinkta nycklar ──────────────────────────

    def test_five_versions_are_one_concept(self):
        """F5.3: ett koncept i fem versioner räknas som 1."""
        previous = None
        for version in range(1, 6):
            previous = self._concept(
                'counter-versioned', version=version,
                status='superseded' if version < 5 else 'stable',
                supersedes=previous)
        self.coworker.invalidate_recordset()
        self.assertEqual(self.coworker.okf_concept_count, 1)

    def test_archived_and_superseded_not_counted(self):
        """F5.3: arkiverade och superseded-koncept räknas inte."""
        self._concept('counter-a')
        self._concept('counter-b')
        self._concept('counter-c')
        self._concept('counter-archived', archived=True)
        self._concept('counter-old', version=1, status='superseded')
        self.coworker.invalidate_recordset()
        self.assertEqual(self.coworker.okf_concept_count, 3)

    def test_other_owners_are_not_counted(self):
        """F5.3: bara medarbetarens egna koncept räknas."""
        other = self.Coworker.create({'name': 'Counters Other'})
        self._concept('counter-mine')
        self.Concept.create({
            'scope': 'coworker', 'owner_coworker_id': other.id,
            'artifact_type_id': self.artifact.id,
            'concept_key': 'counter-theirs', 'version': 1,
            'title': 'theirs', 'summary': 'x',
        })
        self.coworker.invalidate_recordset()
        self.assertEqual(self.coworker.okf_concept_count, 1)
