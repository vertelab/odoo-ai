# -*- coding: utf-8 -*-
"""Integrations-/enhetstester för ai-tool-access-capabilities.

Körs med: checkmodule -d <db> -m ai_agent_core -t
Täcker: group_ids på ai.tool (1.1), _filter_by_access_groups (1.2),
PermissionEngine-deny (1.4) och Tool.group_ids-populering (1.4).
"""

from odoo.tests.common import TransactionCase
from odoo.exceptions import AccessError  # noqa: F401 (framtida ACL-tester)


def _make_tool(env, name, groups=None, code=None, risk='read_only'):
    vals = {
        'name': name,
        'description': f'{name} — testverktyg',
        'risk_level': risk,
        'code': code or (
            'async def execute():\n'
            '    return "ok"'
        ),
    }
    if groups:
        vals['group_ids'] = [(6, 0, groups)]
    return env['ai.tool'].create(vals)


class TestToolAccessGroups(TransactionCase):

    def setUp(self):
        super().setUp()
        # Skapa en res.groups för testerna (ärver base.group_user)
        self.operator_group = self.env['res.groups'].create({
            'name': 'Test Operator Group',
            'category_id': self.env.ref('base.module_category_hidden').id,
        })
        self.tool_gated = _make_tool(
            self.env, 'tool_test_gated', groups=[self.operator_group.id])
        self.tool_open = _make_tool(self.env, 'tool_test_open')

    def test_filter_unrestricted_tools_without_groups(self):
        """Användare utan grupp ser bara obundna verktyg (1.2)."""
        visible = self.env['ai.tool'].search(
            [('id', 'in', (self.tool_gated.id, self.tool_open.id))]
        )._filter_by_access_groups([])
        self.assertIn(self.tool_open, visible)
        self.assertNotIn(self.tool_gated, visible)

    def test_filter_member_of_group_sees_gated_tool(self):
        """Användare i gruppen ser det gruppbundna verktyget (1.2)."""
        visible = self.env['ai.tool'].search(
            [('id', 'in', (self.tool_gated.id, self.tool_open.id))]
        )._filter_by_access_groups([self.operator_group.id])
        self.assertIn(self.tool_gated, visible)
        self.assertIn(self.tool_open, visible)

    def test_filter_other_group_denied(self):
        """Användare i en annan grupp ser inte det gruppbundna verktyget."""
        other = self.env['res.groups'].create({
            'name': 'Other Group',
            'category_id': self.env.ref('base.module_category_hidden').id,
        })
        visible = self.env['ai.tool'].search(
            [('id', 'in', (self.tool_gated.id, self.tool_open.id))]
        )._filter_by_access_groups([other.id])
        self.assertIn(self.tool_open, visible)
        self.assertNotIn(self.tool_gated, visible)


class TestToolAccessPermissionEngine(TransactionCase):

    def setUp(self):
        super().setUp()
        self.operator_group = self.env['res.groups'].create({
            'name': 'Test Operator Group (Engine)',
            'category_id': self.env.ref('base.module_category_hidden').id,
        })
        self.tool_gated = _make_tool(
            self.env, 'tool_engine_gated', groups=[self.operator_group.id])
        self.tool_open = _make_tool(self.env, 'tool_engine_open')

    def test_engine_denies_grouped_tool_for_user_without_group(self):
        """PermissionEngine nekar gruppbundet verktyg utan korsning (1.4)."""
        from odoo.addons.ai_agent_core.core.permission import PermissionEngine
        from odoo.addons.ai_agent_core.core.tools import ai_tool_records_to_tools

        tools = {t.name: t for t in ai_tool_records_to_tools(
            self.tool_gated | self.tool_open, self.env)}
        engine = PermissionEngine(user_group_ids=set())
        d = engine.evaluate('tool_engine_gated', {}, metadata=tools['tool_engine_gated'])
        self.assertFalse(d.allowed)
        self.assertIn('access group', d.reason)

    def test_engine_allows_grouped_tool_for_member(self):
        from odoo.addons.ai_agent_core.core.permission import PermissionEngine
        from odoo.addons.ai_agent_core.core.tools import ai_tool_records_to_tools

        tools = {t.name: t for t in ai_tool_records_to_tools(
            self.tool_gated | self.tool_open, self.env)}
        engine = PermissionEngine(
            user_group_ids={self.operator_group.id})
        d = engine.evaluate('tool_engine_gated', {}, metadata=tools['tool_engine_gated'])
        self.assertTrue(d.allowed)

    def test_tool_dataclass_carries_group_ids(self):
        """ai_tool_records_to_tools populera Tool.group_ids (1.4)."""
        from odoo.addons.ai_agent_core.core.tools import ai_tool_records_to_tools
        tools = ai_tool_records_to_tools(self.tool_gated | self.tool_open, self.env)
        by_name = {t.name: t for t in tools}
        self.assertEqual(
            set(by_name['tool_engine_gated'].group_ids),
            {self.operator_group.id})
        self.assertEqual(by_name['tool_engine_open'].group_ids, [])

    def test_engine_allows_open_tool_without_groups(self):
        """Obundet verktyg passerar även utan kända grupper."""
        from odoo.addons.ai_agent_core.core.permission import PermissionEngine
        from odoo.addons.ai_agent_core.core.tools import ai_tool_records_to_tools

        tools = {t.name: t for t in ai_tool_records_to_tools(
            self.tool_gated | self.tool_open, self.env)}
        engine = PermissionEngine(user_group_ids=set())
        d = engine.evaluate('tool_engine_open', {}, metadata=tools['tool_engine_open'])
        self.assertTrue(d.allowed)


class TestToolDescriptionSerialization(TransactionCase):

    def test_description_serialized_unchanged(self):
        """Strukturerad beskrivning serialiseras komplett (2.3/2.4)."""
        long_desc = (
            'syfte: applicera ett state\n'
            'när: efter diagnos, när åtgärd är bekräftad\n'
            'när inte: föredra state.show_sls först\n'
            'exempel: {"minion": "gw*", "state": "caddy.service"}\n'
            'output: resultat per minion (retcode, changes)\n'
            'guardrail: kräver mänskligt godkännande'
        )
        tool = _make_tool(
            self.env, 'tool_long_desc', risk='destructive', code=None)
        tool.write({'description': long_desc})
        from odoo.addons.ai_agent_core.core.tools import ai_tool_records_to_tools
        core = ai_tool_records_to_tools(tool, self.env)[0]
        # OpenAI-format
        oai = core.to_openai()
        self.assertEqual(oai['function']['description'], long_desc)
        # Anthropic-format
        anth = core.to_anthropic()
        self.assertEqual(anth['description'], long_desc)

    def test_guardrail_text_does_not_gate_execution(self):
        """Guardrail nämns i text, men enforcement ligger i risk_level (2.4)."""
        tool = _make_tool(
            self.env, 'tool_guardrail_text',
            risk='destructive',
            code=None)
        tool.write({'description': (
            'syfte: applicera state\n'
            'guardrail: kräver mänskligt godkännande'
        )})
        from odoo.addons.ai_agent_core.core.tools import ai_tool_records_to_tools
        core = ai_tool_records_to_tools(tool, self.env)[0]
        # Destructive → alltid godkännande via Tool.needs_human_approval
        self.assertTrue(core.needs_human_approval(threshold=0))
        self.assertEqual(core.risk_level, 'destructive')
        # PermissionEngine: destructive utan godkännande nekas i AUTO-läge
        from odoo.addons.ai_agent_core.core.permission import (
            PermissionEngine, PermissionMode)
        engine = PermissionEngine(mode=PermissionMode.AUTO)
        d = engine.evaluate('tool_guardrail_text', {}, metadata=core)
        self.assertTrue(d.allowed or d.needs_user)


class TestCapabilitySerialization(TransactionCase):

    def _build_tools(self, names):
        from odoo.addons.ai_agent_core.core.tools import ai_tool_records_to_tools
        recs = self.env['ai.tool']
        for n in names:
            recs |= _make_tool(self.env, n)
        return ai_tool_records_to_tools(recs, self.env)

    def test_enum_mode_single_tool(self):
        """Enum-läge visar ett tool med operation-enum; medlemmar tas bort (3.3)."""
        from odoo.addons.ai_agent_core.core.tools import (
            ToolRegistry, apply_capability_serialization)
        members = self._build_tools(['t_a', 't_b', 't_c'])
        reg = ToolRegistry()
        reg.register_many(members)
        caps = [{
            'name': 'cap_test',
            'description': 'syfte: testförmåga',
            'member_names': ['t_a', 't_b', 't_c'],
        }]
        suffix = apply_capability_serialization(reg, caps, 'enum')
        self.assertEqual(suffix, '')
        names = [t.name for t in reg.list()]
        self.assertIn('cap_test', names)
        self.assertNotIn('t_a', names)
        self.assertNotIn('t_b', names)
        enum_tool = reg.get('cap_test')
        ops = enum_tool.parameters['properties']['operation']['enum']
        self.assertEqual(set(ops), {'t_a', 't_b', 't_c'})

    def test_enum_mode_splits_over_8(self):
        """Förmåga med >8 operationer delas i ≤8-enheter (3.3 scenario)."""
        from odoo.addons.ai_agent_core.core.tools import (
            ToolRegistry, apply_capability_serialization,
            CAPABILITY_ENUM_MAX_OPS)
        members = self._build_tools([f't_{i}' for i in range(10)])
        reg = ToolRegistry()
        reg.register_many(members)
        caps = [{
            'name': 'cap_big',
            'description': 'stor förmåga',
            'member_names': [f't_{i}' for i in range(10)],
        }]
        apply_capability_serialization(reg, caps, 'enum')
        enum_tools = [t for t in reg.list() if t.name.startswith('cap_big')]
        self.assertEqual(len(enum_tools), 2)
        for t in enum_tools:
            ops = t.parameters['properties']['operation']['enum']
            self.assertLessEqual(len(ops), CAPABILITY_ENUM_MAX_OPS)
        self.assertNotIn('t_0', [t.name for t in reg.list()])

    def test_namespace_mode_prompt(self):
        """Namespace-läge behåller verktygen + injicerar beskrivning (3.4)."""
        from odoo.addons.ai_agent_core.core.tools import (
            ToolRegistry, apply_capability_serialization)
        members = self._build_tools(['t_x', 't_y'])
        reg = ToolRegistry()
        reg.register_many(members)
        caps = [{
            'name': 'cap_ns',
            'description': 'syfte: namespace-förmåga',
            'member_names': ['t_x', 't_y'],
        }]
        suffix = apply_capability_serialization(reg, caps, 'namespace')
        self.assertIn('cap_ns', suffix)
        self.assertIn('syfte: namespace-förmåga', suffix)
        self.assertIn('t_x', suffix)
        # Individuella verktyg finns kvar (parallellitet)
        self.assertIn('t_x', [t.name for t in reg.list()])

    def test_access_filtered_member_hidden_in_both_modes(self):
        """Access-filtrerad medlem dold både som tool och operation (3.5)."""
        from odoo.addons.ai_agent_core.core.tools import (
            ToolRegistry, apply_capability_serialization)
        group = self.env['res.groups'].create({
            'name': 'Cap Group',
            'category_id': self.env.ref('base.module_category_hidden').id,
        })
        secret = _make_tool(self.env, 't_secret', groups=[group.id])
        open_tool = _make_tool(self.env, 't_open')
        # Access-filtrera som användare UTAN gruppen → bara t_open syns
        visible = (secret | open_tool)._filter_by_access_groups([])
        self.assertNotIn(secret, visible)
        self.assertIn(open_tool, visible)
        from odoo.addons.ai_agent_core.core.tools import ai_tool_records_to_tools
        members = ai_tool_records_to_tools(visible, self.env)
        reg = ToolRegistry()
        reg.register_many(members)
        caps = [{
            'name': 'cap_access',
            'description': 'access-test',
            'member_names': ['t_secret', 't_open'],
        }]
        apply_capability_serialization(reg, caps, 'enum')
        enum_tool = reg.get('cap_access')
        ops = enum_tool.parameters['properties']['operation']['enum']
        self.assertEqual(ops, ['t_open'])
        self.assertNotIn('t_secret', ops)
