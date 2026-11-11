# -*- coding: utf-8 -*-
"""Sessionsknappen: räknare med månadssemantik, lista med historiksemantik.

VARFÖR (coworker-session-smart-button): räknaren på `ai.coworker` mäter
innevarande kalendermånad, men klicket byggde SAMMA domän — månaden blev ett
tak i stället för en etikett, och en session från september gick inte att nå
från formuläret.

Dessa tester bevisar:
  1. `_session_domain` ger rätt domän för alla fyra perioderna (T1.3)
  2. månaden beräknas på `create_date`, inte på senaste aktivitet
  3. räknaren och det förvalda filtret ger samma svar (T5.1)
  4. hela historiken är nåbar — åtgärdens domän har ingen datumgräns (T5.2)
  5. knappen ryms på en rad — månaden bor i hjälptexten, inte i en etikettrad
"""

from datetime import date, timedelta

import lxml.etree

from odoo.tests.common import TransactionCase, tagged
from ._test_users import create_user


@tagged('coworker_session_smart_button', 'post_install', '-at_install')
class TestSessionDomain(TransactionCase):
    """T1.3 / T5.1 / T5.2: en definition av månaden, och en lista utan tak."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Session = cls.env['ai.coworker.session']
        cls.Coworker = cls.env['ai.coworker']
        cls.user = cls.env.create_user(name='Smart Button User', login='smartbutton@example.com', email='smartbutton@example.com')
        cls.coworker = cls.Coworker.create({
            'name': 'Smart Button Coworker',
            'status': 'active',
        })
        cls.other = cls.Coworker.create({'name': 'Smart Button Other'})

    # ── hjälpare ────────────────────────────────────────────────────────

    def _session(self, days_ago=0, coworker=None):
        """Skapa en session och flytta dess create_date bakåt."""
        session = self.Session.create({
            'coworker_id': (coworker or self.coworker).id,
            'status': 'active',
            'user_id': self.user.id,
            'name': 'smart-button',
        })
        if days_ago:
            self.env.cr.execute(
                "UPDATE ai_coworker_session "
                "SET create_date = create_date - interval '%s days' "
                "WHERE id = %s", (days_ago, session.id))
            session.invalidate_recordset(['create_date'])
        return session

    def _previous_month_session(self):
        """Session som startade i föregående kalendermånad (första dagen)."""
        session = self._session()
        self.env.cr.execute(
            "UPDATE ai_coworker_session SET create_date = "
            "date_trunc('month', now()) - interval '1 day' WHERE id = %s",
            (session.id,))
        session.invalidate_recordset(['create_date'])
        return session

    # ── 1. Domänen för de fyra perioderna ───────────────────────────────

    def test_all_has_no_date_bound(self):
        """`all` = bara medarbetaren — ingen create_date-gräns."""
        domain = self.Session._session_domain(self.coworker.id, 'all')
        self.assertEqual(domain, [('coworker_id', '=', self.coworker.id)])

    def test_this_month_starts_at_month_start(self):
        """`this_month` jämför mot första dagen i innevarande månad."""
        domain = self.Session._session_domain(self.coworker.id,
                                              'this_month')
        today = date.today()
        self.assertIn(('coworker_id', '=', self.coworker.id), domain)
        self.assertIn(('create_date', '>=', date(today.year, today.month, 1)),
                      domain)
        self.assertEqual(len(domain), 2, 'ingen övre gräns för this_month')

    def test_last_month_is_a_closed_window(self):
        """`last_month` har både undre och övre gräns."""
        domain = self.Session._session_domain(self.coworker.id, 'last_month')
        self.assertEqual(len(domain), 3)
        self.assertIn(('create_date', '<', date.today().replace(day=1)),
                      domain)
        start = self.Session._session_period_start('last_month')
        self.assertLess(start, date.today().replace(day=1))
        # Januari → föregående månad är december föregående år.
        if date.today().month == 1:
            self.assertEqual((start.year, start.month),
                             (date.today().year - 1, 12))

    def test_last_7_days(self):
        """`last_7_days` räknar sju dagar bakåt från idag."""
        domain = self.Session._session_domain(self.coworker.id, 'last_7_days')
        self.assertIn(('create_date', '>=', date.today() - timedelta(days=7)),
                      domain)

    def test_unknown_period_raises(self):
        """Ett stavfel ska inte tyst ge en ofiltrerad domän."""
        with self.assertRaises(ValueError):
            self.Session._session_domain(self.coworker.id, 'this_mnth')

    def test_sessions_button_is_one_line(self):
        """Knappen ryms på en rad: `o_stat_info` har exakt två barn.

        Layouten går inte att rendera i en `TransactionCase`, så beviset är
        arkitekturen: etiketten + siffran, inget mer. En tredje rad
        (månadsetiketten `session_count_label`) gjorde knappen tvåradig och
        sprängde knappraden (session-machine-and-filter D1).
        """
        view = self.env.ref('ai_agent_core.ai_coworker_form')
        # `arch_db` är översatt (xml_translate); `arch_base` är källan utan
        # översättning. Källan är det som ska vara tvåradig eller inte.
        root = lxml.etree.fromstring(view.arch_base.encode())
        infos = root.xpath(
            "//button[@name='action_get_sessions']"
            "/div[@class='o_stat_info']")
        self.assertEqual(len(infos), 1,
                         'knappens o_stat_info ska finnas exakt en gång')
        info = infos[0]
        self.assertEqual(len(info), 2,
                         'o_stat_info ska ha exakt två barn: etikett + siffra')
        labels = [el.text or '' for el in info.iter() if el.text]
        self.assertIn('Sessions', ' '.join(labels))
        self.assertNotIn(
            'session_count_label', lxml.etree.tostring(info, encoding='unicode'),
            'månadsetiketten får inte ligga i knappen — månaden bor i '
            'hjälptexten')
        self.assertNotIn('session_count_label', self.Coworker._fields,
                         'fältet finns bara för den tvåradiga layouten')

    # ── 2. Månaden räknas på create_date ────────────────────────────────

    def test_periods_count_the_right_sessions(self):
        """T1.3: varje period ger rätt antal mot skapandedatum."""
        today_session = self._session()                 # idag
        self._session(days_ago=2)                       # i månaden
        previous = self._previous_month_session()       # förra månaden
        old = self._session(days_ago=400)               # förra året

        this_month = self.Session.search(
            self.Session._session_domain(self.coworker.id, 'this_month'))
        self.assertIn(today_session, this_month)
        self.assertNotIn(previous, this_month)
        self.assertNotIn(old, this_month)

        last_month = self.Session.search(
            self.Session._session_domain(self.coworker.id, 'last_month'))
        self.assertIn(previous, last_month)
        self.assertNotIn(today_session, last_month)

        everything = self.Session.search(
            self.Session._session_domain(self.coworker.id, 'all'))
        self.assertIn(today_session, everything)
        self.assertIn(previous, everything)
        self.assertIn(old, everything)

    def test_activity_this_month_does_not_count_an_old_session(self):
        """Månaden räknas på start, inte på senaste aktivitet."""
        session = self._previous_month_session()
        self.env['ai.coworker.session.line'].create({
            'session_id': session.id, 'role': 'assistant',
            'content': 'aktivitet i dag', 'sequence': 1,
            'token_input': 10, 'sys_multiplier': 1.0})
        self.coworker.invalidate_recordset()
        this_month = self.Session.search(
            self.Session._session_domain(self.coworker.id, 'this_month'))
        self.assertNotIn(session, this_month)
        self.assertEqual(self.coworker.session_count, 0)

    def test_other_coworkers_are_not_counted(self):
        """Domänen är bunden till medarbetaren."""
        self._session()
        self._session(coworker=self.other)
        self.coworker.invalidate_recordset()
        self.assertEqual(self.coworker.session_count, 1)

    # ── 3. Räknaren och filtret ger samma svar ─────────────────────────

    def test_counter_matches_the_default_filter(self):
        """T5.1: siffran = antalet poster med `this_month`-filtret aktivt."""
        self._session()
        self._session(days_ago=1)
        self._previous_month_session()
        self._session(days_ago=400)
        self.coworker.invalidate_recordset()

        action = self.coworker.action_get_sessions()
        self.assertEqual(action['context'].get('search_default_this_month'), 1,
                         'månaden ska vara ett förvalt filter')

        action_rows = self.Session.search(action['domain'])
        filter_rows = self.Session.search(
            self.Session._session_domain(self.coworker.id, 'this_month'))
        self.assertEqual(len(action_rows & filter_rows),
                         self.coworker.session_count,
                         'räknaren och filtret ska visa samma poster')
        self.assertEqual(self.coworker.session_count, 2)

    # ── 4. Hela historiken är nåbar ─────────────────────────────────────

    def test_action_domain_has_no_date_bound(self):
        """T5.2: klickets domän begränsar inte tiden."""
        action = self.coworker.action_get_sessions()
        self.assertNotIn('create_date',
                         [term[0] for term in action['domain']],
                         'månaden får inte ligga i domänen — då blir den ett tak')
        self.assertEqual(action['domain'],
                         [('coworker_id', '=', self.coworker.id)])

    def test_previous_month_session_is_reachable(self):
        """T5.2: en session från förra månaden ingår i listan."""
        previous = self._previous_month_session()
        action = self.coworker.action_get_sessions()
        rows = self.Session.search(action['domain'])
        self.assertIn(previous, rows)
        self.assertGreater(len(rows), self.coworker.session_count)
