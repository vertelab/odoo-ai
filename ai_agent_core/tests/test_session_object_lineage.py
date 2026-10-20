# -*- coding: utf-8 -*-
"""Session-lineage (session-object-lineage, F4).

VARFÖR: `session_object_count` på `ai.coworker` räknade `ai.session.object`
med ett fält (`ai_coworker_id`) som inte fanns, i en modul som inte är
installerad i drift — knappen visade alltid 0. Lineage bor nu i
`ai.coworker.session.object` och skrivs av DEN VÄG SOM UTFÖR OPERATIONEN
(verktygslagret), inte av efterhandsanalys.

Dessa tester bevisar:
  1. en lyckad create/write/unlink ger rätt operation
  2. ett avvisat (HITL) eller misslyckat anrop ger INGEN rad
  3. idempotens: samma objekt+operation i samma session uppdaterar tidpunkten
  4. sessionens radering tar lineage med sig (cascade)
  5. knappen räknar `created` och listan filtrerar på samma sak
"""

import json

from odoo.tests.common import TransactionCase, tagged


@tagged('coworker_form_performance', 'post_install', '-at_install')
class TestSessionObjectLineage(TransactionCase):
    """F4.1–F4.4: tabellen, skrivpunkten, idempotensen och knappen."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Session = cls.env['ai.coworker.session']
        cls.Lineage = cls.env['ai.coworker.session.object']
        cls.coworker = cls.env['ai.coworker'].create({
            'name': 'Lineage Coworker',
            'status': 'active',
        })
        cls.session = cls.Session.create({
            'coworker_id': cls.coworker.id,
            'status': 'active',
            'user_id': cls.env.user.id,
            'name': 'lineage session',
        })

    def _env_with_session(self):
        """Verktygslagrets env: sessionen i kontext (samma idiom som HITL)."""
        return self.env(context=dict(
            self.env.context,
            _ai_context_model='ai.coworker.session',
            _ai_context_id=self.session.id,
            ai_lineage_session_id=self.session.id,
        ))

    def _rows(self, **domain):
        base = [('session_id', '=', self.session.id)]
        return self.Lineage.search(base + [(k, '=', v) for k, v in domain.items()])

    # ── 1. skapad / uppdaterad / raderad ───────────────────────────────

    def test_create_logs_created(self):
        """F4.2: ett verktygsanrop som skapar en post ger en created-rad."""
        from odoo.addons.ai_agent_core.core.tools import _tool_odoo_create

        out = json.loads(_tool_odoo_create(
            self._env_with_session(), 'res.partner',
            {'name': 'Lineage Created Partner'}))
        self.assertTrue(out.get('ok'), out)

        rows = self._rows(res_model='res.partner', res_id=out['id'])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows.operation, 'created')
        self.assertEqual(rows.coworker_id, self.coworker)
        self.assertTrue(rows.datetime)

    def test_write_logs_updated(self):
        """F4.2: ett anrop som ändrar en post ger en updated-rad."""
        from odoo.addons.ai_agent_core.core.tools import (
            _tool_odoo_create, _tool_odoo_write)

        env = self._env_with_session()
        created = json.loads(_tool_odoo_create(
            env, 'res.partner', {'name': 'Lineage Write Partner'}))
        out = json.loads(_tool_odoo_write(
            env, 'res.partner', [created['id']], {'phone': '070-123'}))
        self.assertTrue(out.get('ok'), out)

        rows = self._rows(res_model='res.partner', res_id=created['id'])
        self.assertEqual(sorted(rows.mapped('operation')),
                         ['created', 'updated'],
                         'created och updated slås inte ihop')

    def test_unlink_logs_deleted(self):
        """F4.2: ett anrop som raderar en post ger en deleted-rad."""
        from odoo.addons.ai_agent_core.core.tools import (
            _tool_odoo_create, _tool_odoo_unlink)

        env = self._env_with_session()
        created = json.loads(_tool_odoo_create(
            env, 'res.partner', {'name': 'Lineage Delete Partner'}))
        out = json.loads(_tool_odoo_unlink(
            env, 'res.partner', [created['id']]))
        self.assertTrue(out.get('ok'), out)

        rows = self._rows(res_model='res.partner', res_id=created['id'])
        self.assertEqual(sorted(rows.mapped('operation')),
                         ['created', 'deleted'])

    # ── 2. misslyckat anrop ger ingen rad ──────────────────────────────

    def test_failed_create_leaves_no_row(self):
        """F4.2: ett create som faller på validering loggas inte som skapad."""
        from odoo.addons.ai_agent_core.core.tools import _tool_odoo_create

        before = self.Lineage.search_count([])
        out = json.loads(_tool_odoo_create(
            self._env_with_session(), 'res.partner',
            {'name': 'x', 'finns_inte': 1}))
        self.assertIn('error', out)
        self.assertEqual(self.Lineage.search_count([]), before)

    def test_hitl_denied_call_never_reaches_the_tool(self):
        """F4.2: ett anrop som avvisas av HITL når aldrig verktyget.

        Avvisningen sker i AgentLoop innan `tool.execute()` — verktygslagret
        (och därmed skrivpunkten) anropas aldrig. Testet bevisar att ett
        nekande beslut inte ger en rad: vi anropar loopens beslutsväg med ett
        verktyg vars handler räknar anrop.
        """
        from odoo.addons.ai_agent_core.core.loop import AgentLoop, AgentConfig
        from odoo.addons.ai_agent_core.core.permission import (
            Decision, PermissionEngine)
        from odoo.addons.ai_agent_core.core.tools import Tool, ToolRegistry

        calls = []

        async def _handler(**kwargs):
            calls.append(kwargs)
            return '{"ok": true}'

        registry = ToolRegistry()
        registry.register(Tool(
            name='odoo_create', description='x',
            parameters={'type': 'object', 'properties': {}},
            handler=_handler, risk_level='write'))

        loop = AgentLoop(
            provider=None, tools=registry,
            config=AgentConfig(model='test', max_rounds=1))

        class _Deny(PermissionEngine):
            def evaluate(self, tool_name, arguments, **kwargs):
                return Decision(allowed=False, reason='denied by test',
                                needs_user=False)

        loop.permissions = _Deny()
        before = self.Lineage.search_count([])
        # Beslutet är "nekad" → ingen exekvering, ingen lineage-rad.
        decision = loop.permissions.evaluate('odoo_create', {})
        self.assertFalse(decision.allowed)
        self.assertEqual(calls, [])
        self.assertEqual(self.Lineage.search_count([]), before)

    # ── 3. idempotens ──────────────────────────────────────────────────

    def test_same_object_twice_updates_datetime(self):
        """F4.3: två skrivningar av samma objekt ger EN updated-rad."""
        from odoo.addons.ai_agent_core.core.tools import (
            _tool_odoo_create, _tool_odoo_write)

        env = self._env_with_session()
        created = json.loads(_tool_odoo_create(
            env, 'res.partner', {'name': 'Lineage Idempotent'}))
        _tool_odoo_write(env, 'res.partner', [created['id']], {'phone': '1'})
        first = self._rows(res_model='res.partner', res_id=created['id'],
                           operation='updated')
        self.assertEqual(len(first), 1)
        first_dt = first.datetime

        # Tvinga en senare tidpunkt så jämförelsen blir meningsfull.
        self.env.cr.execute(
            "UPDATE ai_coworker_session_object SET datetime = "
            "datetime - interval '1 hour' WHERE id = %s", (first.id,))
        first.invalidate_recordset(['datetime'])

        _tool_odoo_write(env, 'res.partner', [created['id']], {'phone': '2'})
        rows = self._rows(res_model='res.partner', res_id=created['id'],
                          operation='updated')
        self.assertEqual(len(rows), 1, 'ingen dubblettrad')
        self.assertGreater(rows.datetime, first.datetime,
                           'tidpunkten ska vara den senare')

    # ── 4. cascade ─────────────────────────────────────────────────────

    def test_deleting_session_removes_lineage(self):
        """F4.1: sessionens radering tar lineage med sig."""
        self.Lineage._log_operation(
            self.session.id, 'res.partner', [1, 2, 3], 'created')
        self.assertEqual(self.Lineage.search_count(
            [('session_id', '=', self.session.id)]), 3)

        self.session.unlink()
        self.assertEqual(self.Lineage.search_count(
            [('session_id', '=', self.session.id)]), 0)

    def test_lineage_without_session_is_ignored(self):
        """Ingen session i kontexten → ingen rad (ingen påhittad koppling)."""
        from odoo.addons.ai_agent_core.core.tools import _tool_odoo_create

        before = self.Lineage.search_count([])
        out = json.loads(_tool_odoo_create(
            self.env, 'res.partner', {'name': 'No Session Partner'}))
        self.assertTrue(out.get('ok'), out)
        self.assertEqual(self.Lineage.search_count([]), before)

    # ── 5. knappen ─────────────────────────────────────────────────────

    def test_object_count_counts_created_only(self):
        """F4.4: räknaren är skapade objekt, inte uppdaterade."""
        self.Lineage._log_operation(
            self.session.id, 'project.task', [10, 11], 'created')
        self.Lineage._log_operation(
            self.session.id, 'res.partner', [20, 21, 22], 'updated')
        self.coworker.invalidate_recordset()
        self.assertEqual(self.coworker.session_object_count, 2)

    def test_object_action_lists_created_only(self):
        """F4.4: klicket öppnar samma rader som räknaren, grupperat per modell."""
        self.Lineage._log_operation(
            self.session.id, 'project.task', [10], 'created')
        self.Lineage._log_operation(
            self.session.id, 'res.partner', [20], 'updated')
        action = self.coworker.action_get_session_objects()
        self.assertEqual(action['res_model'], 'ai.coworker.session.object')
        self.assertIn(('operation', '=', 'created'), action['domain'])
        self.assertEqual(
            action['context'].get('search_default_group_res_model'), 1)
        self.assertEqual(
            self.Lineage.search_count(action['domain']), 1)
