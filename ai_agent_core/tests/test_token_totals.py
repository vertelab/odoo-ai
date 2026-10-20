# -*- coding: utf-8 -*-
"""Token-totaler ur EN aggregering (coworker-form-performance F3).

VARFÖR: formuläret för en AI Medarbetare tog 27 s att öppna eftersom fyra
computes var för sig itererade alla session-rader i Python. Nu läser de
samma `_token_totals()` — en `_read_group`. Testerna bevisar att
aggregeringen ger EXAKT samma tal som den gamla Python-loopen, inklusive de
rader som tidigare var `informative = NULL` (de räknades som false i Python
och exkluderades i SQL — två semantiker för samma fråga).

Dessa tester bevisar:
  1. kända rader ger exakta summor per period (månad/dag/vecka per dag)
  2. informativa rader räknas inte, NULL-räder räknas som icke-informativa
  3. databasen och Python ger identiska tal på samma data
  4. en medarbetare utan rader ger nollor (ingen KeyError)
  5. `session_line_count` och `budget_exhausted` läser samma aggregering
"""

from datetime import date, timedelta

from odoo.tests.common import TransactionCase, tagged


@tagged('coworker_form_performance', 'post_install', '-at_install')
class TestTokenTotals(TransactionCase):
    """F3.1/F3.2: `_token_totals()` mot den gamla Python-loopen."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Coworker = cls.env['ai.coworker']
        cls.Session = cls.env['ai.coworker.session']
        cls.Line = cls.env['ai.coworker.session.line']
        cls.user = cls.env['res.users'].create({
            'name': 'Token Totals User',
            'login': 'token_totals@example.com',
            'email': 'token_totals@example.com',
        })
        cls.coworker = cls.Coworker.create({
            'name': 'Token Totals Coworker',
            'status': 'active',
        })
        cls.other = cls.Coworker.create({
            'name': 'Token Totals Other',
            'status': 'active',
        })
        cls.session = cls.Session.create({
            'coworker_id': cls.coworker.id,
            'status': 'active',
            'user_id': cls.user.id,
            'name': 'token totals',
        })
        cls.other_session = cls.Session.create({
            'coworker_id': cls.other.id,
            'status': 'active',
            'user_id': cls.user.id,
            'name': 'token totals other',
        })

    # ── hjälpare ────────────────────────────────────────────────────────

    def _line(self, token_sys, days_ago=0, informative=False,
              session=None):
        """Skapa en rad med exakt `token_sys` och flytta create_date bakåt."""
        line = self.Line.create({
            'session_id': (session or self.session).id,
            'role': 'assistant',
            'content': 'x',
            'token_input': token_sys,
            'sys_multiplier': 1.0,
            'informative': informative,
        })
        if days_ago:
            self.env.cr.execute(
                "UPDATE ai_coworker_session_line "
                "SET create_date = create_date - interval '%s days' "
                "WHERE id = %s",
                (days_ago, line.id),
            )
            line.invalidate_recordset(['create_date'])
        return line

    def _periods(self):
        today = date.today()
        month_start = date(today.year, today.month, 1)
        return month_start, today, today - timedelta(days=7)

    def _old_python_totals(self, coworker, month_start, today, week_start):
        """Den gamla beräkningen — ordagrant, för jämförelsen."""
        month_total = 0
        day_total = 0
        daily = {}
        for line in coworker.session_ids.session_line_ids:
            if not line.create_date:
                continue
            line_day = line.create_date.date()
            if line.informative:
                continue
            if line_day >= month_start:
                month_total += line.token_sys or 0
            if line_day == today:
                day_total += line.token_sys or 0
            if line_day >= week_start:
                daily[line_day] = daily.get(line_day, 0) + (line.token_sys or 0)
        return month_total, day_total, daily

    # ── 1. kända rader → exakta summor ─────────────────────────────────

    def test_known_lines_give_exact_totals(self):
        """F3.1: månad, dag och vecka-per-dag räknas exakt."""
        self._line(100)                 # idag
        self._line(250)                 # idag
        self._line(400, days_ago=2)     # i veckan
        self._line(1_000, days_ago=40)  # förra månaden — bara utanför fönstret

        month_start, today, week_start = self._periods()
        totals = self.Line._token_totals(
            [self.coworker.id], month_start, today, week_start)
        bucket = totals[self.coworker.id]

        self.assertEqual(bucket['day'], 350)
        self.assertEqual(bucket['week_by_day'].get(today), 350)
        # Månadssumman beror på om de 40 dagar gamla raden ligger i denna
        # kalendermånad — jämför därför mot den gamla beräkningen.
        expected_month, _, _ = self._old_python_totals(
            self.coworker, month_start, today, week_start)
        self.assertEqual(bucket['month'], expected_month)

    def test_informative_lines_are_excluded(self):
        """Informativa rader räknas inte (utfall-och-tokenmatning)."""
        self._line(100)
        self._line(9_999, informative=True)

        month_start, today, week_start = self._periods()
        totals = self.Line._token_totals(
            [self.coworker.id], month_start, today, week_start)
        self.assertEqual(totals[self.coworker.id]['month'], 100)
        self.assertEqual(totals[self.coworker.id]['day'], 100)

    def test_null_informative_counts_as_non_informative(self):
        """En rad som var NULL räknas som icke-informativ (D4).

        Kolumnen är NOT NULL sedan 18.0.1.335, så värdet kan inte skrivas som
        NULL via ORM:en. Testet tvingar fram NULL i SQL — exakt det tillstånd
        migrationen backfillar — och bevisar att frågan räknar raden.
        """
        line = self._line(700)
        self.env.cr.execute(
            "ALTER TABLE ai_coworker_session_line "
            "ALTER COLUMN informative DROP NOT NULL")
        try:
            self.env.cr.execute(
                "UPDATE ai_coworker_session_line SET informative = NULL "
                "WHERE id = %s", (line.id,))
            month_start, today, week_start = self._periods()
            totals = self.Line._token_totals(
                [self.coworker.id], month_start, today, week_start)
            self.assertEqual(totals[self.coworker.id]['month'], 700,
                             'en NULL-rad ska räknas som icke-informativ')
        finally:
            self.env.cr.execute(
                "UPDATE ai_coworker_session_line SET informative = false "
                "WHERE informative IS NULL")
            self.env.cr.execute(
                "ALTER TABLE ai_coworker_session_line "
                "ALTER COLUMN informative SET NOT NULL")

    # ── 2. Python och databas ger samma tal ────────────────────────────

    def test_database_equals_python(self):
        """F3.2: `_token_totals()` == den gamla Python-loopen, rad för rad."""
        self._line(111)
        self._line(222, days_ago=1)
        self._line(333, days_ago=3)
        self._line(444, days_ago=40)
        self._line(555, informative=True)
        self._line(666, days_ago=1, informative=True)

        month_start, today, week_start = self._periods()
        totals = self.Line._token_totals(
            [self.coworker.id], month_start, today, week_start)
        bucket = totals[self.coworker.id]
        month_total, day_total, daily = self._old_python_totals(
            self.coworker, month_start, today, week_start)

        self.assertEqual(bucket['month'], month_total)
        self.assertEqual(bucket['day'], day_total)
        self.assertEqual(bucket['week_by_day'], daily)

    def test_totals_are_per_coworker(self):
        """Två medarbetare i samma anrop får sina egna summor."""
        self._line(100)
        self._line(5_000, session=self.other_session)

        month_start, today, week_start = self._periods()
        totals = self.Line._token_totals(
            [self.coworker.id, self.other.id], month_start, today, week_start)
        self.assertEqual(totals[self.coworker.id]['month'], 100)
        self.assertEqual(totals[self.other.id]['month'], 5_000)

    def test_coworker_without_lines_gets_zeros(self):
        """En medarbetare utan rader ger nollor, inte KeyError."""
        month_start, today, week_start = self._periods()
        totals = self.Line._token_totals(
            [self.other.id], month_start, today, week_start)
        self.assertEqual(
            totals[self.other.id], {'month': 0, 'day': 0, 'week_by_day': {}})

    def test_empty_ids_returns_empty(self):
        """Tom id-lista ger tom dict (ingen fråga)."""
        month_start, today, week_start = self._periods()
        self.assertEqual(
            self.Line._token_totals([], month_start, today, week_start), {})

    # ── 3. konsumenterna läser samma aggregering ───────────────────────

    def test_session_line_count_uses_totals(self):
        """F3.4: `session_line_count` = månadssumman ur `_token_totals`."""
        self._line(1_200)
        self._line(9_999, informative=True)
        self.coworker.invalidate_recordset()
        month_start, today, week_start = self._periods()
        totals = self.Line._token_totals(
            [self.coworker.id], month_start, today, week_start)
        self.assertEqual(
            self.coworker.session_line_count, totals[self.coworker.id]['month'])
        self.assertEqual(self.coworker.session_line_count, 1_200)

    def test_budget_state_uses_totals(self):
        """F3.3: budgetstatus härleds ur samma tal."""
        self.coworker.monthly_cap_mtokens = 1
        self._line(850_000)
        self.coworker.invalidate_recordset()
        self.assertTrue(self.coworker.budget_warning)
        self.assertFalse(self.coworker.budget_exhausted)

        self._line(200_000)
        self.coworker.invalidate_recordset()
        self.assertTrue(self.coworker.budget_exhausted)

    def test_burn_forecast_uses_totals(self):
        """F3.5: burn rate = veckans dagar med data, inte alla rader."""
        self._line(1_000_000)              # idag
        self._line(3_000_000, days_ago=2)  # två dagar sedan
        self._line(9_000_000, days_ago=30)  # utanför veckan
        self.coworker.monthly_cap_mtokens = 100
        self.coworker.invalidate_recordset()
        # 4M tokens över 2 dagar med data = 2.0 M/dag.
        self.assertAlmostEqual(self.coworker.burn_rate_mtokens_day, 2.0,
                               places=4)
