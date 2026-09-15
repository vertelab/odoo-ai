# -*- coding: utf-8 -*-
"""Tester för avveckla-builtin-fallbacken.

Körs med: checkmodule -d <db> -m ai_agent_core -t
Täcker:
- Ingen implicit fallback: sessionens verktyg = settings-default + explicit
- Agent utan tool_ids får inga verktyg (tomt register är giltigt)
- Varningen loggas för agent utan verktyg
- load_skill/read_skill kräver explicit tool_ids
- Migrering: kapaciteten bevaras (agent med tomma tool_ids får inte tappa)
"""

import logging

from odoo.tests.common import TransactionCase

# Interna förmågor som aldrig ska läcka in utan explicit tool_ids.
INTERNAL_PREFIXES = (
    'describe_model', 'odoo_search', 'odoo_create', 'odoo_write',
    'odoo_unlink', 'odoo_call_method', 'inventory_', 'builder_',
    'nats_publish', 'graph_', 'graphify_', 'pi_unified_recall',
)


class TestNoImplicitFallback(TransactionCase):
    """Grupp 2 — ingen väg lägger till verktyg som inte valts."""

    def test_empty_coworker_gets_only_settings_default(self):
        """En coworker utan verktyg får exakt settings-default."""
        coworker = self.env['ai.coworker'].create({
            'name': 'ZZ Test Tom Coworker',
        })
        tools, _groups = coworker._session_tools()
        names = set(t.name for t in tools.list())
        defaults = set(
            self.env['ai.agent']._get_default_tool_names())
        self.assertEqual(
            names, defaults & names,
            'Sessionen fick verktyg utanför settings-default')

    def test_no_internal_tools_without_explicit_ids(self):
        """Inga interna verktyg läcker in utan explicit tool_ids."""
        coworker = self.env['ai.coworker'].create({
            'name': 'ZZ Test Intern-Coworker',
        })
        tools, _groups = coworker._session_tools()
        names = [t.name for t in tools.list()]
        internal = [n for n in names if n.startswith(INTERNAL_PREFIXES)]
        self.assertFalse(
            internal,
            'Interna verktyg läckte in utan explicit tool_ids: %s' % internal)

    def test_session_tool_ids_has_no_builtin_fallback(self):
        """_session_tool_ids() innehåller ingen builtin_tools()-väg."""
        import inspect
        src = inspect.getsource(
            self.env['ai.coworker']._session_tool_ids)
        # Docstringen får nämna builtin_tools, men ingen kod får anropa den.
        code_lines = [
            ln for ln in src.splitlines()
            if 'builtin_tools' in ln and not ln.strip().startswith('#')
        ]
        # Tillåt förekomster inuti docstringen (trippelciterade blocket).
        self.assertFalse(
            any('builtin_tools()' in ln and '"""' not in ln
                for ln in code_lines),
            '_session_tool_ids() anropar builtin_tools() i kod')

    def test_default_tool_names_never_returns_internal_tools(self):
        """_get_default_tool_names() ger aldrig interna verktyg."""
        names = self.env['ai.agent']._get_default_tool_names()
        internal = [n for n in names if n.startswith(INTERNAL_PREFIXES)]
        self.assertFalse(
            internal,
            'Settings-default innehåller interna verktyg: %s' % internal)


class TestWarningForAgentsWithoutTools(TransactionCase):
    """Grupp 3 — synlig varning i stället för tystnad."""

    def test_warning_logged_for_agent_without_tools(self):
        """En agent utan tool_ids loggar en varning."""
        coworker = self.env['ai.coworker'].create({
            'name': 'ZZ Test Varning-Coworker',
        })
        agent = self.env['ai.agent'].create({
            'name': 'ZZ Test Varning-Agent',
            'tool_ids': [(5, 0, 0)],
        })
        coworker.write({'agent_ids': [(0, 0, {'agent_id': agent.id})]})

        with self.assertLogs(
                'odoo.addons.ai_agent_core.models.ai_coworker',
                level='WARNING') as cm:
            coworker._session_tools()

        messages = '\n'.join(cm.output)
        self.assertIn(
            'Agent utan verktyg', messages,
            'Varningen för agent utan verktyg loggades inte')
        self.assertIn(agent.name, messages)

    def test_no_warning_when_agent_has_tools(self):
        """En agent med verktyg loggar ingen varning."""
        rec = self.env['ai.tool'].search(
            [('builtin_name', '=', 'odoo_calculator')], limit=1)
        if not rec:
            self.skipTest('odoo_calculator är inte seedad')
        coworker = self.env['ai.coworker'].create({
            'name': 'ZZ Test Tyst-Coworker',
        })
        agent = self.env['ai.agent'].create({
            'name': 'ZZ Test Tyst-Agent',
            'tool_ids': [(6, 0, rec.ids)],
        })
        coworker.write({'agent_ids': [(0, 0, {'agent_id': agent.id})]})

        logger = logging.getLogger(
            'odoo.addons.ai_agent_core.models.ai_coworker')
        with self.assertNoLogs(logger, level='WARNING'):
            coworker._session_tools()


class TestSkillToolsRequireExplicit(TransactionCase):
    """Grupp 5 — load_skill/read_skill kräver explicit tool_ids."""

    def test_read_skill_not_in_session_without_explicit_ids(self):
        """read_skill finns inte i en session utan explicit tool_ids."""
        coworker = self.env['ai.coworker'].create({
            'name': 'ZZ Test Skill-Coworker',
        })
        tools, _groups = coworker._session_tools()
        names = [t.name for t in tools.list()]
        self.assertNotIn(
            'read_skill', names,
            'read_skill läckte in utan explicit tool_ids')

    def test_load_skill_not_in_builtin_tools(self):
        """load_skill är inte en del av builtin_tools() (död kod-vägen)."""
        from odoo.addons.ai_agent_core.core.tools import builtin_tools
        names = [t.name for t in builtin_tools()]
        self.assertNotIn(
            'load_skill', names,
            'load_skill ska inte exponeras via builtin_tools()')

    def test_explicit_read_skill_gives_access(self):
        """Motsatsen: explicit read_skill ger verktyget i sessionen."""
        rec = self.env['ai.tool'].search(
            [('builtin_name', '=', 'read_skill')], limit=1)
        if not rec:
            self.skipTest('read_skill är inte seedad')
        coworker = self.env['ai.coworker'].create({
            'name': 'ZZ Test Skill-Explicit-Coworker',
        })
        agent = self.env['ai.agent'].create({
            'name': 'ZZ Test Skill-Agent',
            'tool_ids': [(6, 0, rec.ids)],
        })
        coworker.write({'agent_ids': [(0, 0, {'agent_id': agent.id})]})

        tools, _groups = coworker._session_tools()
        names = [t.name for t in tools.list()]
        self.assertIn('read_skill', names)


class TestCapacityPreserved(TransactionCase):
    """Grupp 4/6 — migreringen bevarar kapacitet."""

    def test_agent_with_explicit_tools_keeps_them(self):
        """En agent med explicita verktyg behåller exakt dem."""
        recs = self.env['ai.tool'].search(
            [('builtin_name', 'in', ('odoo_calculator', 'odoo_search'))])
        if len(recs) < 2:
            self.skipTest('Seedade verktyg saknas')
        coworker = self.env['ai.coworker'].create({
            'name': 'ZZ Test Kapacitet-Coworker',
        })
        agent = self.env['ai.agent'].create({
            'name': 'ZZ Test Kapacitet-Agent',
            'tool_ids': [(6, 0, recs.ids)],
        })
        coworker.write({'agent_ids': [(0, 0, {'agent_id': agent.id})]})

        tools, _groups = coworker._session_tools()
        names = set(t.name for t in tools.list())
        for rec in recs:
            self.assertIn(
                rec.name, names,
                'Agenten tappade verktyget %s vid sessionbygget' % rec.name)

    def test_union_of_all_linked_agents(self):
        """Sessionen får unionen av alla kopplade agenters verktyg."""
        rec_a = self.env['ai.tool'].search(
            [('builtin_name', '=', 'odoo_calculator')], limit=1)
        rec_b = self.env['ai.tool'].search(
            [('builtin_name', '=', 'odoo_search')], limit=1)
        if not (rec_a and rec_b):
            self.skipTest('Seedade verktyg saknas')
        a1 = self.env['ai.agent'].create({
            'name': 'ZZ Test Union-A1',
            'tool_ids': [(6, 0, rec_a.ids)],
        })
        a2 = self.env['ai.agent'].create({
            'name': 'ZZ Test Union-A2',
            'tool_ids': [(6, 0, rec_b.ids)],
        })
        coworker = self.env['ai.coworker'].create({
            'name': 'ZZ Test Union-Coworker',
            'agent_ids': [
                (0, 0, {'agent_id': a1.id}),
                (0, 0, {'agent_id': a2.id}),
            ],
        })
        tools, _groups = coworker._session_tools()
        names = set(t.name for t in tools.list())
        self.assertIn('odoo_calculator', names)
        self.assertIn('odoo_search', names)
