# -*- coding: utf-8 -*-
"""Tester för budget-hard-cap: deterministisk spärr, blockeringsmatris,
mail.activity, tool-kostnad, session.token_sys."""

from datetime import date, timedelta
from odoo.tests import common, tagged


@tagged('post_install')
class TestBudgetHardCap(common.TransactionCase):
    """Tasks 7.1-7.6: budget-spärr, blockering, aktivitet, tool-kostnad."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Concept = cls.env['ai.okf.concept']
        cls.user = cls.env['res.users'].create({
            'name': 'Budget Test User',
            'login': 'budget_test@example.com',
            'email': 'budget_test@example.com',
        })

    def _mk_coworker(self, cap_mtokens=0, **kw):
        vals = {
            'name': f'Budget Coworker {self.id}',
            'description': 'Budget test coworker',
            'monthly_cap_mtokens': cap_mtokens,
        }
        vals.update(kw)
        return self.env['ai.coworker'].create(vals)

    def _mk_session_line(self, coworker, token_sys=0, days_ago=0):
        session = self.env['ai.coworker.session'].create({
            'coworker_id': coworker.id,
            'status': 'active',
            'user_id': self.user.id,
            'name': 'Budget test session',
        })
        # Skapa line med token_input = token_sys och multiplier 1.0 så
        # compute ger token_sys = token_sys
        line = self.env['ai.coworker.session.line'].create({
            'session_id': session.id,
            'role': 'assistant',
            'content': 'test',
            'token_input': token_sys,
            'sys_multiplier': 1.0,
        })
        if days_ago:
            # Flytta create_date bakåt — raden räknas inte i aktuell månad
            self.env.cr.execute(
                "UPDATE ai_coworker_session_line SET create_date = "
                "create_date - interval '%s days' WHERE id = %s",
                (days_ago, line.id),
            )
        return line

    # ── 7.1 Deterministisk spärr ──

    def test_budget_exhausted_compute(self):
        """budget_exhausted härleds deterministiskt från session_line_count."""
        coworker = self._mk_coworker(cap_mtokens=1)  # 1M = 1_000_000
        self.assertFalse(coworker.budget_exhausted)
        self.assertFalse(coworker.budget_warning)

        # Förbruka 1.2M systemtokens
        self._mk_session_line(coworker, token_sys=1_200_000)
        self.env['ai.coworker'].invalidate_cache(['session_line_count',
                                                   'budget_exhausted',
                                                   'budget_warning'])
        coworker._compute_budget_state()
        self.assertTrue(coworker.budget_exhausted)

    def test_budget_warning_at_80(self):
        """budget_warning vid 80%."""
        coworker = self._mk_coworker(cap_mtokens=1)
        self._mk_session_line(coworker, token_sys=850_000)
        self.env['ai.coworker'].invalidate_cache(['session_line_count',
                                                   'budget_exhausted',
                                                   'budget_warning'])
        coworker._compute_budget_state()
        self.assertTrue(coworker.budget_warning)
        self.assertFalse(coworker.budget_exhausted)

    def test_new_month_auto_opens(self):
        """Ny månad → gamla rader räknas inte → budget öppnas auto."""
        coworker = self._mk_coworker(cap_mtokens=1)
        # Förbruka förra månaden (create_date flyttad 40 dagar bakåt)
        self._mk_session_line(coworker, token_sys=1_500_000, days_ago=40)
        self.env['ai.coworker'].invalidate_cache(['session_line_count',
                                                   'budget_exhausted'])
        coworker._compute_budget_state()
        self.assertFalse(coworker.budget_exhausted,
                         'Förra månadens rader ska inte räknas')

    def test_higher_cap_auto_opens(self):
        """Höjd budget → budget öppnas auto (compute räknar om)."""
        coworker = self._mk_coworker(cap_mtokens=1)
        self._mk_session_line(coworker, token_sys=1_200_000)
        self.env['ai.coworker'].invalidate_cache(['session_line_count',
                                                   'budget_exhausted'])
        coworker._compute_budget_state()
        self.assertTrue(coworker.budget_exhausted)

        # Höj taket → auto-öppnas
        coworker.monthly_cap_mtokens = 2
        self.env['ai.coworker'].invalidate_cache(['budget_exhausted'])
        coworker._compute_budget_state()
        self.assertFalse(coworker.budget_exhausted)

    # ── 7.2 Blockeringsmatris ──

    def test_run_returns_budget_message(self):
        """run() returnerar 'Budget slut' utan att köra LLM."""
        coworker = self._mk_coworker(cap_mtokens=1)
        self._mk_session_line(coworker, token_sys=1_200_000)
        self.env['ai.coworker'].invalidate_cache(['session_line_count',
                                                   'budget_exhausted'])
        coworker._compute_budget_state()

        result = coworker.run(prompt='test prompt')
        self.assertIn('Budget slut', result)

    # ── 7.3 mail.activity ──

    def test_budget_activity_created_once(self):
        """mail.activity skapas en gång per månad."""
        coworker = self._mk_coworker(cap_mtokens=1)
        self._mk_session_line(coworker, token_sys=1_200_000)
        self.env['ai.coworker'].invalidate_cache(['session_line_count',
                                                   'budget_exhausted'])
        coworker._compute_budget_state()

        coworker._notify_budget_once()
        coworker._notify_budget_once()  # andra anropet ska vara no-op

        activities = self.env['mail.activity'].search([
            ('res_model', '=', 'ai.coworker'),
            ('res_id', '=', coworker.id),
            ('done', '=', False),
        ])
        self.assertEqual(len(activities), 1,
                         'Endast en öppen aktivitet ska finnas')

    def test_budget_activity_unlocked(self):
        """Upplåsning stänger aktiviteten + nollställer notis-månad."""
        coworker = self._mk_coworker(cap_mtokens=1)
        self._mk_session_line(coworker, token_sys=1_200_000)
        self.env['ai.coworker'].invalidate_cache(['session_line_count',
                                                   'budget_exhausted'])
        coworker._compute_budget_state()
        coworker._notify_budget_once()
        self.assertTrue(coworker.cap_notified_month)

        # Höj taket + upplås
        coworker.monthly_cap_mtokens = 2
        self.env['ai.coworker'].invalidate_cache(['budget_exhausted'])
        coworker._compute_budget_state()
        coworker._unlock_budget_activities()

        activities = self.env['mail.activity'].search([
            ('res_model', '=', 'ai.coworker'),
            ('res_id', '=', coworker.id),
            ('done', '=', False),
        ])
        self.assertEqual(len(activities), 0,
                         'Aktiviteten ska vara stängd efter upplåsning')
        self.assertFalse(coworker.cap_notified_month)

    # ── 7.4 Tool-kostnad ──

    def test_tool_sys_token_cost_default(self):
        """ai.tool.sys_token_cost default 500."""
        tool = self.env['ai.tool'].create({
            'name': 'budget_test_tool',
            'description': 'Budget test tool',
        })
        self.assertEqual(tool.sys_token_cost, 500)

    def test_tool_line_token_sys(self):
        """Tool-rad (role='tool') får token_sys via token_input×multiplier."""
        coworker = self._mk_coworker()
        session = self.env['ai.coworker.session'].create({
            'coworker_id': coworker.id,
            'status': 'active',
            'user_id': self.user.id,
            'name': 'Tool test session',
        })
        line = self.env['ai.coworker.session.line'].create({
            'session_id': session.id,
            'role': 'tool',
            'tool_name': 'graph_query',
            'content': 'preview',
            'token_input': 500,
            'sys_multiplier': 1.0,
        })
        self.assertEqual(line.token_sys, 500,
                         'Tool-rad ska bära 500 systemtokens via compute')

    # ── 7.5 session.token_sys ──

    def test_session_token_sys(self):
        """session.token_sys = Σ lines token_sys."""
        coworker = self._mk_coworker()
        session = self.env['ai.coworker.session'].create({
            'coworker_id': coworker.id,
            'status': 'active',
            'user_id': self.user.id,
            'name': 'Session sys test',
        })
        for ts in (100, 200, 500):
            self.env['ai.coworker.session.line'].create({
                'session_id': session.id,
                'role': 'assistant',
                'content': 'test',
                'token_input': ts,
                'sys_multiplier': 1.0,
            })
        session._compute_token_sys()
        self.assertEqual(session.token_sys, 800)

    # ── 7.6 Döda fält borta ──

    def test_dead_fields_removed(self):
        """Döda fält finns inte i modellen."""
        Coworker = self.env['ai.coworker']
        self.assertNotIn('budget_kr_monthly', Coworker._fields)
        self.assertNotIn('max_actions_per_day', Coworker._fields)
        self.assertNotIn('cap_exhausted', Coworker._fields)
        self.assertNotIn('cap_warning_sent', Coworker._fields)
        self.assertNotIn('reset_cap', Coworker._fields)

        Session = self.env['ai.coworker.session']
        self.assertNotIn('cost_estimated', Session._fields)

        Agent = self.env['ai.agent']
        self.assertNotIn('budget_used', Agent._fields)
