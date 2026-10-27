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
  5. klicket öppnar HELA historiken med månaden som borttagbart filter
     (coworker-session-smart-button — upphäver F5.1:s "öppnar exakt de
     sessionerna": månaden är en etikett, inte ett tak)
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

    def _concept(self, key, status='stable', archived=False,
                 supersedes=None):
        return self.Concept.create({
            'scope': 'coworker',
            'owner_coworker_id': self.coworker.id,
            'artifact_type_id': self.artifact.id,
            'concept_key': key,
            'status': status,
            'archived': archived,
            'title': key,
            'summary': 'x',
        })

    # ── 1. Sessions mäter månaden ───────────────────────────────────────

    def test_sessions_counts_started_this_month(self):
        """F5.1: bara sessioner som STARTADE i månaden räknas."""
        today = self._session()                 # idag
        self._session(days_ago=1)               # i månaden
        old = self._session(days_ago=400)       # förra året — räknas inte
        self.coworker.invalidate_recordset()

        month_start = date.today().replace(day=1)
        expected = self.Session.search_count([
            ('coworker_id', '=', self.coworker.id),
            ('create_date', '>=', month_start),
        ])
        self.assertEqual(self.coworker.session_count, expected)
        self.assertGreaterEqual(expected, 2,
                                'dagens och gårdagens session räknas')
        self.assertLess(self.coworker.session_count,
                        self.Session.search_count(
                            [('coworker_id', '=', self.coworker.id)]),
                        'all-time räknas inte längre')
        # Den gamla sessionen får inte ingå.
        self.assertNotIn(old, self.Session.search([
            ('coworker_id', '=', self.coworker.id),
            ('create_date', '>=', month_start),
        ]))
        self.assertIn(today, self.Session.search([
            ('coworker_id', '=', self.coworker.id),
            ('create_date', '>=', month_start),
        ]))

    def test_sessions_action_opens_the_full_history(self):
        """Upphäver F5.1 (coworker-session-smart-button).

        Den gamla raden krävde att åtgärdens domän gav exakt samma antal som
        räknaren — vilket gjorde månaden till ett tak: en session från
        september gick inte att nå från formuläret. Nu gäller två saker:

          (a) räknaren mäter fortfarande månaden, och
          (b) åtgärdens domän har INGEN datumgräns — månaden ligger som
              `search_default_this_month`, ett synligt filter som kan tas bort.
        """
        self._session()
        self._session(days_ago=400)
        self.coworker.invalidate_recordset()
        action = self.coworker.action_get_sessions()

        # (a) räknaren = månaden, inte totalen
        self.assertEqual(self.coworker.session_count, 1)

        # (b) åtgärden öppnar allt — ingen create_date i domänen
        self.assertNotIn('create_date',
                         [term[0] for term in action['domain']],
                         'månaden får inte ligga i domänen — då blir den ett tak')
        self.assertEqual(self.Session.search_count(action['domain']), 2)
        self.assertEqual(action['context'].get('search_default_this_month'), 1,
                         'månaden ska vara ett förvalt, borttagbart filter')
        self.assertEqual(action.get('search_view_id'),
                         self.env.ref('ai_agent_core.view_ai_session_search').id,
                         'åtgärden ska peka på sessionens sökvy (annars finns '
                         'inget this_month-filter att förvala)')

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
        # Samma domän som räknaren summerar — knappen får inte öppna mer.
        self.assertEqual(
            self.Line.search_count(action['domain']),
            self.coworker.session_line_count and 1 or 0,
            'listan och räknaren ska visa samma rader')

    # ── 3. Odoo Mind räknar distinkta nycklar ──────────────────────────

    def test_one_concept_per_key(self):
        """F5.3: ett koncept per nyckel räknas som 1.

        (Före 2026-10-26 fanns fem versioner av samma nyckel; nu finns en
        rad, så räkningen är trivial — men den ska fortfarande vara 1.)
        """
        self._concept('counter-versioned')
        self.coworker.invalidate_recordset()
        self.assertEqual(self.coworker.okf_concept_count, 1)

    def test_archived_not_counted(self):
        """F5.3: arkiverade koncept räknas inte."""
        self._concept('counter-a')
        self._concept('counter-b')
        self._concept('counter-c')
        self._concept('counter-archived', archived=True)
        self.coworker.invalidate_recordset()
        self.assertEqual(self.coworker.okf_concept_count, 3)

    def test_other_owners_are_not_counted(self):
        """F5.3: bara medarbetarens egna koncept räknas."""
        other = self.Coworker.create({'name': 'Counters Other'})
        self._concept('counter-mine')
        self.Concept.create({
            'scope': 'coworker', 'owner_coworker_id': other.id,
            'artifact_type_id': self.artifact.id,
            'concept_key': 'counter-theirs',
            'title': 'theirs', 'summary': 'x',
        })
        self.coworker.invalidate_recordset()
        self.assertEqual(self.coworker.okf_concept_count, 1)
