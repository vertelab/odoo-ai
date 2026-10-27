# -*- coding: utf-8 -*-
"""Budget-meddelandet ska nå ALLA kanaler — inte bara två av fyra.

Bakgrund (drift 2026-10-27): Infrastructure Operator hade förbrukat 7,55M av
dagstaket på 1M (755%). I DM/kanal kom ett meddelande, men i web-UI och via
OpenAI-API:t fick användaren **tyst ingenting** — ingen förklaring, ingen
ledtråd om att taket öppnar sig vid midnatt. Månadstaket fanns i alla fyra
kanaler; dagstaket bara i två.

Fixen är EN källa: `ai.coworker.budget_block_message()`. Dessa tester låser
att den finns, att den prioriterar rätt tak, och att den säger något begripligt.
"""

from odoo.tests.common import TransactionCase, tagged


@tagged('budget', 'ai_core', 'post_install', '-at_install')
class TestBudgetBlockMessage(TransactionCase):

    def setUp(self):
        super().setUp()
        self.Coworker = self.env['ai.coworker']
        self.coworker = self.Coworker.create({
            'name': 'Budget Test',
            'orchestration_mode': 'single',
        })

    def test_no_block_when_under_cap(self):
        """Inget tak nått → (None, None), inte en tom sträng."""
        code, msg = self.coworker.budget_block_message()
        self.assertIsNone(code)
        self.assertIsNone(msg)

    def test_daily_exhausted_has_priority(self):
        """Dagstak vinner över månadstak — det öppnar sig själv vid midnatt."""
        self.coworker.write({
            'daily_cap_mtokens': 1.0,
            'daily_budget_used_mtokens': 7.55,
            'monthly_cap_mtokens': 10.0,
            'started_mtokens': 99.0,
        })
        self.coworker.invalidate_recordset()
        code, msg = self.coworker.budget_block_message()
        self.assertEqual(code, 'daily_budget_exhausted')
        self.assertIn('Dagsbudget slut', msg)
        self.assertIn('midnatt', msg)
        # Siffrorna ska vara med — annars går det inte att förstå läget.
        self.assertIn('7.55', msg)
        self.assertIn('755', msg)  # procent

    def test_monthly_exhausted_when_daily_is_fine(self):
        """Bara månadstaket nått → månadskoden, inte dagskoden."""
        self.coworker.write({
            'daily_cap_mtokens': 100.0,
            'daily_budget_used_mtokens': 1.0,
            'monthly_cap_mtokens': 10.0,
            'started_mtokens': 99.0,
            'monthly_budget_soft': False,
        })
        self.coworker.invalidate_recordset()
        code, msg = self.coworker.budget_block_message()
        self.assertEqual(code, 'budget_exhausted')
        self.assertIn('månadstaket', msg)

    def test_soft_monthly_cap_does_not_block(self):
        """Mjukt månadstak ska varna men köra vidare — inte blockera."""
        self.coworker.write({
            'daily_cap_mtokens': 100.0,
            'daily_budget_used_mtokens': 1.0,
            'monthly_cap_mtokens': 10.0,
            'started_mtokens': 99.0,
            'monthly_budget_soft': True,
        })
        self.coworker.invalidate_recordset()
        code, _msg = self.coworker.budget_block_message()
        self.assertIsNone(code)

    def test_message_names_the_coworker(self):
        """Användaren ska se VILKEN medarbetare som är stoppad."""
        self.coworker.write({
            'daily_cap_mtokens': 1.0,
            'daily_budget_used_mtokens': 2.0,
        })
        self.coworker.invalidate_recordset()
        _code, msg = self.coworker.budget_block_message()
        self.assertIn('Budget Test', msg)

    def test_all_channels_use_the_shared_source(self):
        """Regressionsvakt: ingen kanal får ha sin egen budget-text kvar.

        Fällan var att varje kanal formulerade sig själv och att två av dem
        glömde dagsbudgeten. Om någon återinför en hårdkodad sträng här
        fallerar detta test.
        """
        import inspect
        from odoo.addons.ai_agent_core.controllers import openai_api, stream

        for mod in (openai_api, stream):
            src = inspect.getsource(mod)
            self.assertIn(
                'budget_block_message', src,
                '%s använder inte den gemensamma budget-källan' % mod.__name__)
