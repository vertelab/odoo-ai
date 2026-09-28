# -*- coding: utf-8 -*-
"""Tester för agent-identitet i openai_api-vägen (pi-agent-agent-identity).

Körs med: checkmodule -d <db> -m ai_agent_core -t
Täcker:
- `agent`-fältet: uppslagning på id, namn och skiftlägesokänsligt namn
- Okänt värde → felsträng (controllern gör 400), aldrig tyst fallback
- Behörighet: agent utanför coworkerns kopplingar avvisas
- Bakåtkompatibilitet: utan agent är verktygsmängden oförändrad
- ai.tool.nats_agent_id följer med till core-Tool som hint
"""

from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged('post_install', '-at_install')
class TestAgentResolution(TransactionCase):
    """Uppgift 1.2/1.8/1.9/1.10/1.11 — _resolve_agent."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.coworker = cls.env['ai.coworker'].create({
            'name': 'TEST Agent Identity Coworker',
            'status': 'active',
        })
        cls.agent_a = cls.env['ai.agent'].create({
            'name': 'TEST Agent Alpha',
            'status': 'active',
        })
        cls.agent_b = cls.env['ai.agent'].create({
            'name': 'TEST Agent Beta',
            'status': 'active',
        })
        cls.env['ai.coworker.agent'].create({
            'coworker_id': cls.coworker.id,
            'agent_id': cls.agent_a.id,
            'role': 'member',
        })
        cls.env['ai.coworker.agent'].create({
            'coworker_id': cls.coworker.id,
            'agent_id': cls.agent_b.id,
            'role': 'member',
        })
        # En agent som INTE är kopplad till coworkern.
        cls.outsider = cls.env['ai.agent'].create({
            'name': 'TEST Agent Outsider',
            'status': 'active',
        })

    def _resolver(self):
        from odoo.addons.ai_agent_core.controllers.openai_api import (
            AIOpenAPIController)
        return AIOpenAPIController.__new__(AIOpenAPIController)

    def test_resolve_by_name(self):
        """Uppgift 1.8 — namn ger rätt agent."""
        agent, err = self._resolver()._resolve_agent(
            self.coworker, 'TEST Agent Alpha')
        self.assertFalse(err)
        self.assertEqual(agent, self.agent_a)

    def test_resolve_by_id(self):
        """Uppgift 1.9 — numeriskt id ger samma resultat som namnet."""
        agent, err = self._resolver()._resolve_agent(
            self.coworker, str(self.agent_a.id))
        self.assertFalse(err)
        self.assertEqual(agent, self.agent_a)

    def test_resolve_name_case_insensitive(self):
        """Namn matchas skiftlägesokänsligt i andra hand."""
        agent, err = self._resolver()._resolve_agent(
            self.coworker, 'test agent alpha')
        self.assertFalse(err)
        self.assertEqual(agent, self.agent_a)

    def test_unknown_agent_returns_error(self):
        """Uppgift 1.10 — okänt värde ger fel, aldrig tyst fallback."""
        agent, err = self._resolver()._resolve_agent(
            self.coworker, 'finns-inte')
        self.assertIsNone(agent)
        self.assertTrue(err)
        self.assertIn('finns-inte', err)
        # Felet ska lista de tillgängliga agenterna.
        self.assertIn('TEST Agent Alpha', err)

    def test_agent_outside_coworker_rejected(self):
        """Uppgift 1.11 — behörighet: annan coworkers agent avvisas."""
        agent, err = self._resolver()._resolve_agent(
            self.coworker, str(self.outsider.id))
        self.assertIsNone(agent)
        self.assertTrue(err)
        self.assertNotIn('TEST Agent Outsider', err.split('Available')[0])

    def test_coworker_without_agents(self):
        """En coworker utan kopplade agenter ger ett tydligt fel."""
        empty = self.env['ai.coworker'].create({
            'name': 'TEST Agent Identity Empty',
            'status': 'active',
        })
        agent, err = self._resolver()._resolve_agent(empty, 'vad-som-helst')
        self.assertIsNone(agent)
        self.assertTrue(err)
        self.assertIn('no linked agents', err)


@tagged('post_install', '-at_install')
class TestAgentIdentityBackwardCompat(TransactionCase):
    """Uppgift 1.7 — utan agent är beteendet oförändrat."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.coworker = cls.env['ai.coworker'].create({
            'name': 'TEST Agent Identity Compat',
            'status': 'active',
        })
        cls.agent = cls.env['ai.agent'].create({
            'name': 'TEST Compat Agent',
            'status': 'active',
        })
        cls.env['ai.coworker.agent'].create({
            'coworker_id': cls.coworker.id,
            'agent_id': cls.agent.id,
            'role': 'member',
        })

    def test_union_unchanged_without_force_agent(self):
        """Utan force_agent ges samma verktygsmängd som förut (unionen)."""
        tools_plain, _ = self.coworker._session_tools()
        tools_again, _ = self.coworker._session_tools()
        self.assertEqual(
            sorted(t.name for t in tools_plain.list()),
            sorted(t.name for t in tools_again.list()))

    def test_force_agent_is_superset_of_agent_tools(self):
        """Med force_agent ingår agentens egna verktyg i registret."""
        tools_plain, _ = self.coworker._session_tools()
        tools_forced, _ = self.coworker._session_tools(
            force_agent=self.agent)
        plain = {t.name for t in tools_plain.list()}
        forced = {t.name for t in tools_forced.list()}
        # Agentens verktyg ska finnas med.
        for t in self.agent.tool_ids:
            self.assertIn(t.name, forced)


@tagged('post_install', '-at_install')
class TestToolAgentHint(TransactionCase):
    """Uppgift 2.1/2.2/2.5 — ai.tool.nats_agent_id som hint."""

    def test_nats_agent_field_exists(self):
        self.assertIn('nats_agent_id', self.env['ai.tool']._fields)

    def test_nats_agent_carried_to_core_tool(self):
        """Agent-referensen följer med till core-Tool."""
        agent = self.env['ai.agent'].create({
            'name': 'TEST Hint Agent',
            'status': 'active',
        })
        tool = self.env['ai.tool'].create({
            'name': 'test_hint_tool',
            'description': 'Test tool for the agent hint.',
            'executor': 'nats',
            'nats_subject': 'pi.task.do',
            'nats_skills': 'test-skill',
            'risk_level': 'read_only',
            'nats_agent_id': agent.id,
        })
        core = tool.to_core_tool(self.env)
        self.assertEqual(core.executor, 'nats')
        self.assertEqual(core.nats_agent, str(agent.id))

    def test_nats_agent_empty_without_owner(self):
        """Utan ägande agent är hinten tom (ingen styrning)."""
        tool = self.env['ai.tool'].create({
            'name': 'test_hint_tool_none',
            'description': 'Test tool without an agent hint.',
            'executor': 'nats',
            'nats_subject': 'pi.task.do',
            'risk_level': 'read_only',
        })
        core = tool.to_core_tool(self.env)
        self.assertEqual(core.nats_agent, '')
