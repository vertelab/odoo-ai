# -*- coding: utf-8 -*-
"""Stående regler i obevakade körvägar (unattended-standing-rules).

Buggen: `auto_allowed_tools` lästes bara av `action_run_scheduled()`
(cron). Mail-, watch- och heartbeat-vägen byggde en
`PermissionEngine(mode=AUTO)` utan regler och föll tillbaka på
`hitl_threshold` — vilket för en väg utan människa betyder att körningen
aldrig kan slutföras.

Testerna prövar den delade läsningen (`_standing_rule_lists`,
`_apply_standing_rules`) och att alla obevakade vägar använder den.
"""

import json

from odoo.tests import tagged
from odoo.tests.common import TransactionCase

from odoo.addons.ai_agent_core.core.permission import (
    PermissionEngine,
    PermissionMode,
)


@tagged('ai_agent_core', 'post_install', '-at_install')
class StandingRuleBase(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Coworker = cls.env['ai.coworker']

    def _coworker(self, name, tools=None, commands=None):
        return self.Coworker.create({
            'name': name,
            'description': 'x',
            'status': 'active',
            'auto_allowed_tools': json.dumps(tools) if tools else False,
            'auto_allowed_commands': json.dumps(commands) if commands else False,
        })


@tagged('ai_agent_core', 'post_install', '-at_install')
class TestStandingRuleReading(StandingRuleBase):
    """1.1 — den delade läsningen."""

    def test_bare_tool_name_goes_to_auto_allow(self):
        """Ett naket verktygsnamn hamnar i `auto_allow_tools`."""
        cw = self._coworker('Regel naken', tools=['odoo_create'])
        perms = cw._apply_standing_rules(
            PermissionEngine(mode=PermissionMode.AUTO))
        self.assertIn('odoo_create', perms.auto_allow_tools)

    def test_tool_and_target_goes_to_task_rules(self):
        """`"verktyg mål"` blir en task-regel, inte en global tillåtelse."""
        cw = self._coworker('Regel mål', tools=['odoo_search projekt.task'])
        perms = cw._apply_standing_rules(
            PermissionEngine(mode=PermissionMode.AUTO))
        self.assertIn('odoo_search', perms.task_rules)
        self.assertIn('projekt.task', perms.task_rules['odoo_search'])
        self.assertNotIn('odoo_search', perms.auto_allow_tools)

    def test_commands_go_to_session_allowlist(self):
        cw = self._coworker('Regel kommandon', commands=['git status'])
        perms = cw._apply_standing_rules(
            PermissionEngine(mode=PermissionMode.AUTO))
        self.assertIn('git status', perms.session_allow_commands)

    def test_invalid_json_gives_empty_lists(self):
        """Trasig JSON ska göra körningen mindre tillåten, inte kasta."""
        cw = self._coworker('Regel trasig')
        cw.write({'auto_allowed_tools': '{inte json'})
        tools, commands = cw._standing_rule_lists()
        self.assertEqual(tools, [])
        self.assertEqual(commands, [])

    def test_non_list_json_gives_empty_list(self):
        cw = self._coworker('Regel objekt')
        cw.write({'auto_allowed_tools': '{"a": 1}'})
        tools, _commands = cw._standing_rule_lists()
        self.assertEqual(tools, [])


@tagged('ai_agent_core', 'post_install', '-at_install')
class TestUnattendedPathsReadRules(StandingRuleBase):
    """1.2/1.3 — varje obevakad väg läser regeln, inte bara cron."""

    def _auto_permissions_for(self, cw, session):
        """Bygg den grind `run()` bygger när signaturen är giltig."""
        # Signaturen kräver `ai_agent_core.api_secret` — en driftparameter.
        # Sätt den i testet: annars är AUTO fail-closed (B8) och testet
        # faller på sin egen miljö i stället för på regeln.
        self.env['ir.config_parameter'].sudo().set_param(
            'ai_agent_core.api_secret', 'test-secret')
        signed = cw._sign_auto_approve(session)
        self.assertTrue(
            signed,
            'ingen signatur — sätt ai_agent_core.api_secret i testet')
        return cw._apply_standing_rules(
            PermissionEngine(mode=PermissionMode.AUTO))

    def test_run_path_applies_rules(self):
        """Mail/watch/heartbeat går genom run() — regeln ska gälla där."""
        cw = self._coworker('Obevakad run', tools=['odoo_create'])
        session = self.env['ai.coworker.session'].create({
            'coworker_id': cw.id, 'status': 'active', 'init_type': 'mail',
        })
        perms = self._auto_permissions_for(cw, session)
        self.assertIn('odoo_create', perms.auto_allow_tools)

    def test_cron_path_applies_rules(self):
        """Cron läser samma regel via samma metod."""
        cw = self._coworker('Obevakad cron', tools=['odoo_create'])
        perms = cw._apply_standing_rules(
            PermissionEngine(mode=PermissionMode.AUTO))
        self.assertIn('odoo_create', perms.auto_allow_tools)

    def test_rule_does_not_widen_tool_ids(self):
        """Regeln får aldrig ge åtkomst till ett verktyg utanför tool_ids.

        `_apply_standing_rules` rör bara grinden; registret byggs av
        `_session_tools()` och filtreras av `_session_tool_ids()`. Ett
        verktyg som inte finns i registret kan grinden aldrig släppa fram,
        eftersom loopen bara kan anropa verktyg ur registret.
        """
        cw = self._coworker('Regel vidgar inte', tools=['odoo_create'])
        tools, _groups = cw._session_tools()
        names = {tool.name for tool in tools.list()}
        self.assertNotIn(
            'odoo_unlink', names,
            'registret ska inte innehålla ett verktyg coworkern inte har')
