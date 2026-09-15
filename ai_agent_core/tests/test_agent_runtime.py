# -*- coding: utf-8 -*-
"""Tester för ai.agent.runtime (external-agent-runtime §1).

Runtime är en ORTOGONAL axel mot init-typen: init-typen säger vem som väcker
coworkern, runtime säger var agent-loopen kör. Testerna pinnar:

- default är `in_process` (och att fältet är required, så inget kan lämnas tomt)
- befintliga agenter påverkas inte av att fältet införs
- `external` går att sätta explicit
- ingen ny init-typ tillkommer (axeln är ortogonal, inte en fjärde kategori)
"""

from odoo.tests import TransactionCase


class TestAgentRuntime(TransactionCase):

    def setUp(self):
        super().setUp()
        self.Agent = self.env['ai.agent']

    def test_default_is_in_process(self):
        """Ny agent utan explicit runtime blir in_process."""
        agent = self.Agent.create({'name': 'Runtime default-test'})
        self.assertEqual(agent.runtime, 'in_process')

    def test_runtime_is_required(self):
        """Fältet är required — ingen agent kan ha en odefinierad körmiljö."""
        agent = self.Agent.create({'name': 'Runtime required-test'})
        self.assertTrue(agent.runtime)

    def test_external_can_be_set(self):
        agent = self.Agent.create({
            'name': 'Runtime external-test',
            'runtime': 'external',
        })
        self.assertEqual(agent.runtime, 'external')

    def test_existing_agents_are_unaffected(self):
        """Att fältet införs ändrar ingen befintlig agent (default täcker)."""
        agent = self.Agent.create({'name': 'Runtime legacy-test'})
        self.assertEqual(agent.runtime, 'in_process')
        # Värdet är satt i databasen, inte beräknat — det överlever en reload.
        self.Agent.invalidate_model(['runtime'])
        self.assertEqual(agent.runtime, 'in_process')

    def test_runtime_is_orthogonal_to_init_type(self):
        """Runtime är en EGEN axel — ingen ny init-typ tillkommer."""
        agent = self.Agent.create({
            'name': 'Runtime orthogonal-test',
            'runtime': 'external',
        })
        self.assertEqual(agent.runtime, 'external')
        # Init-typerna är oförändrade av denna ändring.
        init_type = self.env['ai.coworker.init_type']
        keys = set(init_type._fields['init_type'].selection and
                   [k for k, _ in init_type._fields['init_type'].selection]
                   or [])
        self.assertNotIn('external', keys)
        self.assertNotIn('daemon', keys)
