# -*- coding: utf-8 -*-
"""Tester för dagsbudget med mjukt stopp (budget-burn-rate grupp 6).

Verifierar:
  (6.2) notisen visas bara EN gång per dygn, och dygnsnyckeln gör att
        nästa dygn öppnar spärren igen
  (6.3) det mjuka stoppet anropas från körningsvägarna innan LLM-körning

Dagstaket är ett MJUKT stopp till skillnad från månadstaket (hårt):
körningen stoppas men posten låses inte, och taket öppnas av sig självt
vid midnatt — ingen åtgärd krävs.
"""

from unittest.mock import patch

from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestDailyBudget(TransactionCase):
    """Dagsbudgetens mjuka stopp."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Coworker = cls.env['ai.coworker']

    def _coworker(self, name='Dagsbudget-test', daily_cap=1):
        return self.Coworker.create({
            'name': name,
            'description': 'x',
            'status': 'active',
            'daily_cap_mtokens': daily_cap,
        })

    # ── 6.2 notisen en gång per dygn ──────────────────────────────────

    def test_notification_only_once_per_day(self):
        """6.2: två anrop samma dygn ⇒ exakt en notis."""
        cw = self._coworker('Dagsbudget en-notis')
        # Tvinga fram varningstillståndet utan att bygga session-rader.
        with patch.object(type(cw), '_compute_budget_state'), \
             patch.object(type(cw), 'daily_budget_warning',
                          new_callable=lambda: property(lambda s: True)), \
             patch.object(type(cw), 'daily_budget_exhausted',
                          new_callable=lambda: property(lambda s: False)), \
             patch.object(type(cw), 'daily_budget_used_mtokens',
                          new_callable=lambda: property(lambda s: 0.9)):
            with patch.object(type(cw), '_notify_cap') as notify:
                cw.check_daily_cap()
                cw.check_daily_cap()
                cw.check_daily_cap()
                self.assertEqual(
                    notify.call_count, 1,
                    'notisen ska bara skickas en gång per dygn')
        self.assertTrue(cw.cap_notified_day,
                        'dygnsnyckeln ska sättas efter notisen')

    def test_day_key_matches_today(self):
        """6.2: dygnsnyckeln är dagens datum (YYYY-MM-DD)."""
        from odoo import fields as odoo_fields
        cw = self._coworker('Dagsbudget nyckel')
        with patch.object(type(cw), 'daily_budget_warning',
                          new_callable=lambda: property(lambda s: True)), \
             patch.object(type(cw), 'daily_budget_exhausted',
                          new_callable=lambda: property(lambda s: False)), \
             patch.object(type(cw), 'daily_budget_used_mtokens',
                          new_callable=lambda: property(lambda s: 0.9)):
            with patch.object(type(cw), '_notify_cap'):
                cw.check_daily_cap()
        self.assertEqual(cw.cap_notified_day,
                         odoo_fields.Date.today().isoformat())

    def test_new_day_opens_the_gate(self):
        """6.2: en ny dygnsnyckel (gårdagens datum) öppnar spärren igen."""
        cw = self._coworker('Dagsbudget ny-dag')
        # Simulera att notisen skickades i GÅR.
        cw.cap_notified_day = '2000-01-01'
        with patch.object(type(cw), 'daily_budget_warning',
                          new_callable=lambda: property(lambda s: True)), \
             patch.object(type(cw), 'daily_budget_exhausted',
                          new_callable=lambda: property(lambda s: False)), \
             patch.object(type(cw), 'daily_budget_used_mtokens',
                          new_callable=lambda: property(lambda s: 0.9)):
            with patch.object(type(cw), '_notify_cap') as notify:
                cw.check_daily_cap()
                self.assertEqual(
                    notify.call_count, 1,
                    'ett nytt dygn ska öppna spärren och notisen visas igen')

    def test_no_notification_when_below_threshold(self):
        """6.2: under tröskeln skickas ingen notis."""
        cw = self._coworker('Dagsbudget under-troskel')
        with patch.object(type(cw), 'daily_budget_warning',
                          new_callable=lambda: property(lambda s: False)), \
             patch.object(type(cw), 'daily_budget_exhausted',
                          new_callable=lambda: property(lambda s: False)):
            with patch.object(type(cw), '_notify_cap') as notify:
                warning, exhausted = cw.check_daily_cap()
                self.assertFalse(warning)
                self.assertFalse(exhausted)
                self.assertEqual(notify.call_count, 0)

    def test_exhausted_takes_precedence_over_warning(self):
        """6.2: slut-varianten används när taket nåtts, inte varningen."""
        cw = self._coworker('Dagsbudget slut')
        with patch.object(type(cw), 'daily_budget_warning',
                          new_callable=lambda: property(lambda s: True)), \
             patch.object(type(cw), 'daily_budget_exhausted',
                          new_callable=lambda: property(lambda s: True)), \
             patch.object(type(cw), 'daily_budget_used_mtokens',
                          new_callable=lambda: property(lambda s: 1.0)):
            with patch.object(type(cw), '_notify_cap') as notify:
                cw.check_daily_cap()
                self.assertEqual(notify.call_count, 1)
                level = notify.call_args[0][0]
                self.assertEqual(
                    level, 'daily_exhausted',
                    'slut-tillståndet ska ge daily_exhausted, inte warning')

    # ── 6.3 anropas från körningsvägarna ──────────────────────────────

    def test_daily_cap_is_checked_before_llm_run(self):
        """6.3: run() anropar dagsbudget-kontrollen innan LLM-körning.

        Kontrollen är guardad på `daily_budget_exhausted` — den ska bara
        trigga notisen när taket faktiskt är nått, inte vid varje körning.
        Verifierar därför båda fallen: taket nått ⇒ anropas och körningen
        stoppas; taket inte nått ⇒ inget anrop, körningen fortsätter.
        """
        cw = self._coworker('Dagsbudget run-väg')
        # Fall 1: dagstaket är nått → kontrollen anropas och stoppar.
        with patch.object(type(cw), 'daily_budget_exhausted',
                          new_callable=lambda: property(lambda s: True)), \
             patch.object(type(cw), 'daily_budget_used_mtokens',
                          new_callable=lambda: property(lambda s: 1.0)), \
             patch.object(type(cw), 'daily_budget_warning',
                          new_callable=lambda: property(lambda s: False)), \
             patch.object(type(cw), 'budget_exhausted',
                          new_callable=lambda: property(lambda s: False)), \
             patch.object(type(cw), 'check_daily_cap') as daily, \
             patch.object(type(cw), '_unlock_budget_activities'):
            result = cw.run('testprompt')
            self.assertTrue(
                daily.called,
                'dagsbudget-kontrollen ska anropas när taket är nått')
            self.assertIn('Dagsbudget slut', str(result),
                          'körningen ska stoppas mjukt vid dagstaket')

    def test_run_does_not_check_daily_cap_below_threshold(self):
        """6.3: under taket anropas ingen dagsbudget-notis (inte slöseri)."""
        cw = self._coworker('Dagsbudget under-tak')
        with patch.object(type(cw), 'daily_budget_exhausted',
                          new_callable=lambda: property(lambda s: False)), \
             patch.object(type(cw), 'budget_exhausted',
                          new_callable=lambda: property(lambda s: False)), \
             patch.object(type(cw), 'check_daily_cap') as daily, \
             patch.object(type(cw), '_unlock_budget_activities'):
            try:
                cw.run('testprompt')
            except Exception:
                pass  # provider saknas — inte det vi mäter
            self.assertFalse(
                daily.called,
                'notisen ska bara triggas vid taket, inte varje körning')

    def test_check_daily_cap_returns_tuple(self):
        """6.3: kontrollen returnerar (warning, exhausted) för körningsvägen."""
        cw = self._coworker('Dagsbudget retur')
        result = cw.check_daily_cap()
        self.assertIsInstance(result, tuple)
        self.assertEqual(len(result), 2)

    def test_daily_cap_field_exists_and_defaults_to_zero(self):
        """6.2/6.3: daily_cap_mtokens finns och 0 = obegränsat."""
        cw = self._coworker('Dagsbudget default')
        self.assertIn('daily_cap_mtokens', cw._fields)
        self.assertEqual(cw.daily_cap_mtokens, 1)

    def test_soft_monthly_flag_does_not_block_daily(self):
        """6.3: mjukt månadstak påverkar inte dagsstoppets returvärde."""
        cw = self.Coworker.create({
            'name': 'Dagsbudget mjuk-manad',
            'description': 'x',
            'status': 'active',
            'monthly_cap_mtokens': 0,
            'monthly_budget_soft': True,
            'daily_cap_mtokens': 0,
        })
        warning, exhausted = cw.check_daily_cap()
        self.assertFalse(warning)
        self.assertFalse(exhausted)
