# -*- coding: utf-8 -*-
"""Budget-meddelandet ska nå ALLA kanaler — inte bara två av fyra.

Bakgrund (drift 2026-10-27): Infrastructure Operator hade förbrukat 11,3M av
dagstaket på 1M (1126%). I DM/kanal kom ett meddelande, men i web-UI och via
OpenAI-API:t fick användaren **tyst ingenting** — ingen förklaring, ingen
ledtråd om att taket öppnar sig vid midnatt.

Två separata fel hittades:

1. `openai_api.py` (/ai/openai/<id>/v1/chat/completions) kontrollerade bara
   `budget_exhausted`, som med `monthly_budget_soft=True` aldrig blockerar.
2. `/ai/v1/chat/completions` (stream.py) — den route Pi faktiskt använder —
   hade INGEN budgetkontroll alls. En medarbetare 1126% över dagstaket
   svarade glatt HTTP 200.

Fixen är EN källa: `ai.coworker.budget_block_message()`.
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
        """Inget tak nått -> (None, None), inte en tom sträng."""
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
        self.assertIn('7.55', msg)
        self.assertIn('755', msg)

    def test_monthly_exhausted_when_daily_is_fine(self):
        """Bara månadstaket nått -> månadskoden, inte dagskoden."""
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
        glömde dagsbudgeten — och att /ai/v1-route:n saknade kontrollen helt.
        """
        import inspect
        from odoo.addons.ai_agent_core.controllers import openai_api, stream

        for mod in (openai_api, stream):
            src = inspect.getsource(mod)
            self.assertIn(
                'budget_block_message', src,
                '%s använder inte den gemensamma budget-källan' % mod.__name__)

    def test_ai_v1_route_has_budget_check(self):
        """/ai/v1/chat/completions (stream.py) måste kontrollera budgeten.

        Pi använder denna route. Den saknade kontrollen helt fram till
        2026-10-27, vilket är varför en medarbetare 1126% över dagstaket
        svarade HTTP 200.
        """
        import inspect
        from odoo.addons.ai_agent_core.controllers import stream

        src = inspect.getsource(stream)
        self.assertIn('insufficient_quota', src,
                      '/ai/v1-route:n svarar inte med insufficient_quota')
        self.assertIn('daily_budget_exhausted', src,
                      '/ai/v1-route:n kontrollerar inte dagsbudgeten')
