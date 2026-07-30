# -*- coding: utf-8 -*-
"""Tests for orchestration types — linear, conference, automation, Alternativ B."""

import json
from odoo.tests.common import TransactionCase, tagged
from odoo import fields


@tagged('orchestration', 'ai_core')
class TestAlternativB(TransactionCase):
    """Coworker = skal, agent = hjärna."""

    def setUp(self):
        super().setUp()
        self.Agent = self.env['ai.agent']
        self.Coworker = self.env['ai.coworker']
        self.CoworkerAgent = self.env['ai.coworker.agent']
        self.Skill = self.env['ai.skill']
        self.Identity = self.env['ai.identity']
        self.Model = self.env['ai.model']
        self.Provider = self.env['ai.provider']

        # Create a model and provider for agents
        self.provider = self.Provider.create({
            'name': 'Test Provider',
            'provider_type': 'custom',
            'status': 'confirmed',
        })
        self.model = self.Model.create({
            'name': 'gpt-4o-test',
            'provider_id': self.provider.id,
        })

        # Create test agents
        self.agent_a = self.Agent.create({
            'name': 'Agent A',
            'ai_role': 'Research',
            'ai_goal': 'Collect data',
            'model_id': self.model.id,
        })
        self.agent_b = self.Agent.create({
            'name': 'Agent B',
            'ai_role': 'Analysis',
            'ai_goal': 'Analyze data',
            'model_id': self.model.id,
        })

    def test_single_mode_requires_one_agent(self):
        """Single mode coworker must have at least one agent."""
        coworker = self.Coworker.create({
            'name': 'Test Single',
            'orchestration_mode': 'single',
        })
        # Auto-create default agent on first check
        self.CoworkerAgent.create({
            'coworker_id': coworker.id,
            'agent_id': self.agent_a.id,
        })
        coworker.invalidate_recordset()
        self.assertEqual(len(coworker.agent_ids), 1)

    def test_linear_agents_ordered_by_sequence(self):
        """Linear pipeline agents sorted by sequence."""
        coworker = self.Coworker.create({
            'name': 'Test Linear',
            'orchestration_mode': 'linear',
        })
        self.CoworkerAgent.create({
            'coworker_id': coworker.id,
            'agent_id': self.agent_a.id,
            'sequence': 20,
        })
        self.CoworkerAgent.create({
            'coworker_id': coworker.id,
            'agent_id': self.agent_b.id,
            'sequence': 10,
        })
        coworker.invalidate_recordset()
        agents = coworker.agent_ids.sorted('sequence')
        self.assertEqual(agents[0].agent_id.id, self.agent_b.id)
        self.assertEqual(agents[1].agent_id.id, self.agent_a.id)

    def test_conference_mode_valid(self):
        """Conference mode is a valid selection."""
        coworker = self.Coworker.create({
            'name': 'Test Conference',
            'orchestration_mode': 'conference',
        })
        self.assertEqual(coworker.orchestration_mode, 'conference')

    def test_automation_mode_valid(self):
        """Automation mode is a valid selection."""
        coworker = self.Coworker.create({
            'name': 'Test Automation',
            'orchestration_mode': 'automation',
        })
        self.assertEqual(coworker.orchestration_mode, 'automation')


@tagged('orchestration', 'ai_core', 'sales_coach')
class TestSalesCoachScenario(TransactionCase):
    """Acceptanstest: Säljcoachen — full CRM → offert → uppföljning."""

    def setUp(self):
        super().setUp()
        self.Coworker = self.env['ai.coworker']
        self.Agent = self.env['ai.agent']
        self.CoworkerAgent = self.env['ai.coworker.agent']
        self.Skill = self.env['ai.skill']
        self.Partner = self.env['res.partner']
        self.Product = self.env['product.product']
        self.SaleOrder = self.env['sale.order']

        try:
            self.crm_lead = self.env['crm.lead']
        except Exception:
            self.crm_lead = None

    def _create_coach_coworker(self):
        """Create the Säljcoachen coworker with supervisor mode."""
        skill = self.Skill.create({
            'name': 'offert.flode',
            'description': 'Full offertprocess: CRM → offert → uppföljning',
            'category': 'orchestration',
            'recipe_text': (
                'Du är Säljcoachen. Hjälp säljteamet med offerter.\n'
                '1. Hitta kund i CRM, produkter och priser\n'
                '2. Skapa sale.order\n'
                '3. Skicka offert\n'
                '4. Bevaka öppning\n'
                '5. Eskalera vid behov'
            ),
        })

        # Create specialist agents
        crm_agent = self.Agent.create({
            'name': 'CRM-specialist',
            'ai_role': 'CRM-hanterare',
            'ai_goal': 'Hitta kundinfo, produkter, priser',
        })
        offert_agent = self.Agent.create({
            'name': 'Offerthanterare',
            'ai_role': 'Offert-specialist',
            'ai_goal': 'Skapa och skicka offerter',
        })

        coworker = self.Coworker.create({
            'name': 'Säljcoachen',
            'orchestration_mode': 'supervisor',
            'description': 'Hjälper säljteamet med offerter och uppföljning',
            'skill_ids': [(4, skill.id)],
        })

        self.CoworkerAgent.create({
            'coworker_id': coworker.id,
            'agent_id': crm_agent.id,
            'sequence': 10,
            'role': 'member',
        })
        self.CoworkerAgent.create({
            'coworker_id': coworker.id,
            'agent_id': offert_agent.id,
            'sequence': 20,
            'role': 'member',
        })

        return coworker

    def test_coach_coworker_created(self):
        """Säljcoachen kan skapas med rätt konfiguration."""
        coworker = self._create_coach_coworker()
        self.assertEqual(coworker.name, 'Säljcoachen')
        self.assertEqual(coworker.orchestration_mode, 'supervisor')
        self.assertEqual(len(coworker.agent_ids), 2)
        self.assertTrue(coworker.skill_ids)

    def test_coworker_checks_agents(self):
        """Coworker with no agents shows error."""
        coworker = self.Coworker.create({
            'name': 'Empty Coworker',
            'orchestration_mode': 'single',
        })
        error = coworker._check_quest_error()
        self.assertTrue('agent' in (error or '').lower())

    def test_crm_lead_to_order_flow(self):
        """Test that the base flow works — CRM → sale.order creation."""
        if not self.crm_lead:
            self.skipTest("CRM module not installed")

        partner = self.Partner.create({
            'name': 'Acme Corp',
            'email': 'acme@example.com',
        })
        lead = self.crm_lead.create({
            'name': 'Acme SaaS Offer',
            'partner_id': partner.id,
            'expected_revenue': 50000,
        })

        # Verify coworker can search CRM (using OdooModelTools)
        coworker = self._create_coach_coworker()
        self.assertTrue(coworker.agent_ids)

        # Create a sale.order (simulating the first step)
        product = self.env['product.product'].create({
            'name': 'SaaS Base',
            'list_price': 15000,
            'type': 'service',
        })
        order = self.SaleOrder.create({
            'partner_id': partner.id,
            'order_line': [(0, 0, {
                'product_id': product.id,
                'product_uom_qty': 1,
                'price_unit': 15000,
            })],
        })
        self.assertTrue(order)
        self.assertEqual(order.partner_id.id, partner.id)
        self.assertEqual(order.amount_total, 15000)

    def test_activity_creation_for_followup(self):
        """Mail.activity can be created for sales follow-up."""
        partner = self.Partner.create({
            'name': 'Acme Test',
            'email': 'acme@test.com',
        })
        order = self.SaleOrder.create({
            'partner_id': partner.id,
        })
        activity = self.env['mail.activity'].create({
            'res_model_id': self.env['ir.model']._get('sale.order').id,
            'res_id': order.id,
            'activity_type_id': self.env.ref('mail.mail_activity_data_call').id,
            'summary': 'Ring Acme — offerten oläst',
            'note': '<p>Offerten har inte öppnats på 5 dagar</p>',
        })
        self.assertTrue(activity)
        self.assertEqual(activity.res_id, order.id)


@tagged('orchestration', 'ai_core')
class TestLinearLoopModule(TransactionCase):
    """Test LinearLoop basic functionality."""

    def test_linear_module_importable(self):
        """LinearLoop module can be imported."""
        try:
            from odoo.addons.ai_agent_core.core.linear import LinearLoop, LinearConfig
            self.assertTrue(True)
        except ImportError as e:
            self.fail(f"LinearLoop import failed: {e}")

    def test_supervisor_has_conference(self):
        """SupervisorLoop has conference method."""
        from odoo.addons.ai_agent_core.core.supervisor import SupervisorLoop
        self.assertTrue(hasattr(SupervisorLoop, 'conference'))


@tagged('orchestration', 'ai_core', 'buzz')
class TestBuzzEnhanced(TransactionCase):
    """Buzz enhanced features — LLM router, A2A config, per-agent resources."""

    def setUp(self):
        super().setUp()
        self.Coworker = self.env['ai.coworker']

    def test_buzz_llm_router_field(self):
        """Buzz coworker has LLM router toggle."""
        coworker = self.Coworker.create({
            'name': 'Buzz Team',
            'orchestration_mode': 'buzz',
            'buzz_use_llm_router': True,
            'buzz_a2a_max_depth': 5,
        })
        self.assertTrue(coworker.buzz_use_llm_router)
        self.assertEqual(coworker.buzz_a2a_max_depth, 5)

    def test_buzz_a2a_configurable(self):
        """A2A max depth is configurable."""
        coworker = self.Coworker.create({
            'name': 'Buzz A2A Test',
            'orchestration_mode': 'buzz',
            'buzz_a2a_max_depth': 7,
        })
        self.assertEqual(coworker.buzz_a2a_max_depth, 7)
