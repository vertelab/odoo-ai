# -*- coding: utf-8 -*-
"""Tester för coworker-delegation — fält, liveness, verktyg, grindar.

Täcker tasks 1.1–1.4 (modellfält + livstecken), 2.x (delegeringsverktyget),
3.x (mottagbarhet), 4.x (tillitsärvning), 5.x (kvot/uppmärksamhet),
6.x (kollegakatalog) och 7.x (observability/attribution).
"""

from unittest.mock import patch

from odoo import fields
from odoo.exceptions import ValidationError
from odoo.tests.common import TransactionCase, tagged

from ._config_param_guard import ConfigParamGuardedCase


class DelegationTestBase(TransactionCase):
    """Gemensam uppsättning: avdelningar, coworkers, agent, session."""

    def setUp(self):
        super().setUp()
        self.Coworker = self.env['ai.coworker']
        self.Task = self.env['ai.org.task']
        self.Session = self.env['ai.coworker.session']
        self.Dept = self.env['hr.department']

        # Avdelningsträd: drift (förälder) → support (barn); ekonomi separat.
        self.dept_drift = self.Dept.create({'name': 'Drift-DELTEST'})
        self.dept_support = self.Dept.create({
            'name': 'Support-DELTEST', 'parent_id': self.dept_drift.id})
        self.dept_ekonomi = self.Dept.create({'name': 'Ekonomi-DELTEST'})

        # Användare för uppmärksamhet.
        n = self.env['res.users'].search_count([])
        self.owner = self.env['res.users'].create({
            'name': 'Delegation Owner %s' % n,
            'login': 'deleg_owner_%s' % n,
            'password': 'x',
            'groups_id': [(6, 0, [self.env.ref('base.group_user').id])],
        })

        # Coworkers.
        self.chief = self._cw('Driftchef-DELTEST', self.dept_drift,
                              threshold='high_risk')
        self.worker = self._cw('Driftare-DELTEST', self.dept_drift,
                               threshold='autonomous',
                               chat_user_id=self.owner.id)
        self.support = self._cw('Support-DELTEST', self.dept_support)
        self.accountant = self._cw('Redovisning-DELTEST', self.dept_ekonomi)

        # Agent för beställaren (beställare_ref kräver en ai.agent).
        self.provider = self.env['ai.provider'].create({
            'name': 'Deleg Provider', 'provider_type': 'custom',
            'status': 'confirmed'})
        self.model = self.env['ai.model'].create({
            'name': 'deleg-model', 'provider': self.provider.id})
        self.agent = self.env['ai.agent'].create({
            'name': 'Deleg Agent', 'model_id': self.model.id})
        self.env['ai.coworker.agent'].create({
            'coworker_id': self.chief.id, 'agent_id': self.agent.id})
        # Redovisning får också en agent (beställare_ref kräver en ai.agent).
        self.env['ai.coworker.agent'].create({
            'coworker_id': self.accountant.id, 'agent_id': self.agent.id})

    def _cw(self, name, dept, threshold='high_risk', **kw):
        vals = {
            'name': name,
            'status': 'active',
            'orchestration_mode': 'single',
            'department_id': dept.id,
            'hitl_threshold': threshold,
            'heartbeat_enabled': True,
        }
        vals.update(kw)
        cw = self.Coworker.create(vals)
        # Ge alla en färsk lyckad körning → mottagbara som default.
        cw.last_successful_run = fields.Datetime.now()
        return cw

    def _requester_env(self, requester, task=None):
        """Miljö med session-kontext så att _current_coworker hittar den."""
        session = self.Session.create({
            'coworker_id': requester.id,
            'user_id': self.owner.id,
            'status': 'active',
            'init_type': 'heartbeat',
            'ai_task_id': task.id if task else False,
        })
        return requester.with_context(
            _ai_context_model='ai.coworker.session',
            _ai_context_id=session.id), session


@tagged('coworker_delegation')
class TestDelegationFields(DelegationTestBase):
    """1.1–1.2: nya fält på ai.org.task och ai.coworker."""

    def test_task_delegation_fields_exist(self):
        task = self.Task.create({'name': 'Fälttest'})
        self.assertEqual(task.beställare_trust, 0)
        self.assertEqual(task.delegation_depth, 0)
        self.assertFalse(task.parent_task_id)
        self.assertIn('delegated_task_ids', task._fields)

    def test_task_delegation_fields_readonly(self):
        task = self.Task.create({
            'name': 'Readonly-test',
            'beställare_trust': 2, 'delegation_depth': 1})
        self.assertTrue(task._fields['beställare_trust'].readonly)
        self.assertTrue(task._fields['delegation_depth'].readonly)

    def test_coworker_last_successful_run_field(self):
        cw = self.Coworker.create({'name': 'Liveness-test', 'status': 'active'})
        self.assertIn('last_successful_run', cw._fields)


@tagged('coworker_delegation')
class TestLastSuccessfulRun(ConfigParamGuardedCase):
    """1.3–1.4: verkligt livstecken vs rotationsmarkör."""

    def _coworker(self, **kw):
        vals = {'name': 'HB-test', 'status': 'active',
                'orchestration_mode': 'single'}
        vals.update(kw)
        return self.env['ai.coworker'].create(vals)

    def test_heartbeat_all_does_not_set_liveness(self):
        cw = self._coworker(heartbeat_enabled=True)
        self.assertFalse(cw.last_successful_run)
        cw._heartbeat_all()
        cw.invalidate_recordset()
        self.assertFalse(
            cw.last_successful_run,
            'last_successful_run får inte sättas av en tick utan körning')
        self.assertTrue(cw.last_heartbeat)

    def test_successful_run_sets_liveness(self):
        cw = self._coworker(heartbeat_enabled=True)
        self.env['ai.coworker.session'].create({
            'coworker_id': cw.id, 'status': 'active',
            'init_type': 'heartbeat', 'job_prompt': 'Gör något',
            'heartbeat_pending': True})
        with patch.object(type(cw), 'run', lambda *a, **k: None):
            self.env['ai.coworker']._process_heartbeat_sessions()
        cw.invalidate_recordset()
        self.assertTrue(cw.last_successful_run)

    def test_failed_run_does_not_set_liveness(self):
        cw = self._coworker(heartbeat_enabled=True)
        self.env['ai.coworker.session'].create({
            'coworker_id': cw.id, 'status': 'active',
            'init_type': 'heartbeat', 'job_prompt': 'Gör något',
            'heartbeat_pending': True})

        def _boom(*a, **k):
            raise RuntimeError('simulerat haveri')

        with patch.object(type(cw), 'run', _boom):
            self.env['ai.coworker']._process_heartbeat_sessions()
        cw.invalidate_recordset()
        self.assertFalse(cw.last_successful_run)


@tagged('coworker_delegation')
class TestDelegationDirection(DelegationTestBase):
    """2.2: riktningsregeln (neråt/sidledes tillåts, uppåt/tvärs nekas)."""

    def test_sideways_same_department_allowed(self):
        ok, _ = self.worker._delegation_target_allowed(self.chief)
        self.assertTrue(ok, 'sidledes inom avdelningen ska tillåtas')

    def test_downward_allowed(self):
        ok, _ = self.chief._delegation_target_allowed(self.support)
        self.assertTrue(ok, 'neråt i avdelningsträdet ska tillåtas')

    def test_upward_denied(self):
        ok, reason = self.support._delegation_target_allowed(self.chief)
        self.assertFalse(ok, 'uppåt ska nekas (eskalering)')
        self.assertIn('riktning', reason.lower())

    def test_across_departments_denied(self):
        ok, _ = self.worker._delegation_target_allowed(self.accountant)
        self.assertFalse(ok, 'tvärs över avdelningar ska nekas')

    def test_missing_department_is_hard_error(self):
        orphan = self.Coworker.create({
            'name': 'Utan avdelning', 'status': 'active'})
        ok, reason = self.worker._delegation_target_allowed(orphan)
        self.assertFalse(ok)
        self.assertIn('avdelning', reason.lower())


@tagged('coworker_delegation')
class TestDelegationCreate(DelegationTestBase):
    """2.3–2.6, 5.1–5.2: skapande, djup, kvot, uppmärksamhet."""

    def test_task_created_with_correct_fields(self):
        env, _ = self._requester_env(self.chief)
        task = env._delegate_task_to(self.worker, 'Kolla loggarna på gw0')
        self.assertEqual(task.coworker_id, self.worker)
        self.assertEqual(task.status, 'todo')
        self.assertEqual(task.beställare_ref.id, self.agent.id)
        self.assertEqual(task.beställare_ref._name, 'ai.agent')
        self.assertEqual(task.beställare_trust, 1)  # chief = high_risk
        self.assertEqual(task.delegation_depth, 1)

    def test_depth_increments_per_step(self):
        env, sess = self._requester_env(self.chief)
        parent = env._delegate_task_to(self.worker, 'Steg 1')
        # worker delegerar vidare (sidledes till chief) — djup 2.
        env2, _ = self._requester_env(self.worker, task=parent)
        child = env2._delegate_task_to(self.chief, 'Steg 2')
        self.assertEqual(child.delegation_depth, 2)
        self.assertEqual(child.parent_task_id, parent)

    def test_depth_cap_blocks_chain(self):
        self.env['ir.config_parameter'].sudo().set_param(
            'ai_agent_core.delegation_max_depth', '1')
        env, sess = self._requester_env(self.chief)
        parent = env._delegate_task_to(self.worker, 'Steg 1')
        env2, _ = self._requester_env(self.worker, task=parent)
        with self.assertRaises(ValidationError) as cm:
            env2._delegate_task_to(self.chief, 'Steg 2')
        self.assertIn('maxdjup', str(cm.exception))

    def test_quota_blocks(self):
        self.env['ir.config_parameter'].sudo().set_param(
            'ai_agent_core.delegation_max_per_recipient_per_day', '1')
        env, _ = self._requester_env(self.chief)
        env._delegate_task_to(self.worker, 'Första')
        with self.assertRaises(ValidationError) as cm:
            env._delegate_task_to(self.worker, 'Andra')
        self.assertIn('kvot', str(cm.exception))

    def test_empty_description_rejected(self):
        env, _ = self._requester_env(self.chief)
        with self.assertRaises(ValidationError):
            env._delegate_task_to(self.worker, '   ')

    def test_activity_created_for_recipient(self):
        env, _ = self._requester_env(self.chief)
        task = env._delegate_task_to(self.worker, 'Kolla loggarna')
        acts = self.env['mail.activity'].search([
            ('res_id', '=', task.id),
            ('res_model', '=', 'ai.org.task'),
            ('user_id', '=', self.owner.id)])
        self.assertTrue(acts, 'mottagarens ägare ska få en aktivitet')


@tagged('coworker_delegation')
class TestReceptivity(DelegationTestBase):
    """3.1–3.3: mottagbarhetsgrinden."""

    def test_receptive_when_alive(self):
        ok, _ = self.worker._is_receptive()
        self.assertTrue(ok)

    def test_budget_exhausted_not_receptive(self):
        self.worker.monthly_cap_mtokens = 1
        # Skapa förbrukning över taket via en session-rad.
        sess = self.Session.create({
            'coworker_id': self.worker.id, 'status': 'active',
            'user_id': self.owner.id})
        self.env['ai.coworker.session.line'].create({
            'session_id': sess.id, 'role': 'assistant', 'content': 'x',
            'token_input': 2_000_000, 'token_output': 0,
            'sys_multiplier': 1.0})
        self.worker.invalidate_recordset()
        ok, reason = self.worker._is_receptive()
        self.assertFalse(ok)
        self.assertIn('budget', reason.lower())

    def test_missing_liveness_not_receptive(self):
        self.worker.last_successful_run = False
        ok, reason = self.worker._is_receptive()
        self.assertFalse(ok)
        self.assertIn('livstecken', reason.lower())

    def test_stale_liveness_not_receptive(self):
        from datetime import timedelta
        self.worker.last_successful_run = (
            fields.Datetime.now() - timedelta(hours=5))
        ok, _ = self.worker._is_receptive()
        self.assertFalse(ok)

    def test_delegation_to_unreceptive_denied(self):
        self.worker.last_successful_run = False
        env, _ = self._requester_env(self.chief)
        with self.assertRaises(ValidationError) as cm:
            env._delegate_task_to(self.worker, 'Kolla loggarna')
        self.assertIn('livstecken', str(cm.exception).lower())
        # Ingen task skapad (inget ruttnande).
        self.assertEqual(self.Task.search_count([
            ('coworker_id', '=', self.worker.id),
            ('description', '=', 'Kolla loggarna')]), 0)


@tagged('coworker_delegation')
class TestDelegatedTrust(DelegationTestBase):
    """4.1–4.3: tillitsärvning i PermissionEngine."""

    def test_min_trust_applied(self):
        from odoo.addons.ai_agent_core.core.permission import (
            PermissionEngine, PermissionMode)
        engine = PermissionEngine(mode=PermissionMode.AUTO)
        # beställare steg 0, utförare steg 2 → effektivt 0.
        engine.delegated_trust = min(0, 2)
        d = engine.evaluate('odoo_write', {'model': 'res.partner'})
        self.assertTrue(d.needs_user,
                        'låg beställare ska tvinga mänskligt godkännande')

    def test_autonomous_requester_not_tightened(self):
        from odoo.addons.ai_agent_core.core.permission import (
            PermissionEngine, PermissionMode)
        engine = PermissionEngine(mode=PermissionMode.AUTO)
        engine.delegated_trust = 2  # min(2,2)
        d = engine.evaluate('odoo_write', {'model': 'res.partner'})
        self.assertFalse(d.needs_user,
                         'autonom beställare + autonom utförare = oförändrat')

    def test_hard_stop_not_bypassed_by_delegation(self):
        from odoo.addons.ai_agent_core.core.permission import (
            PermissionEngine, PermissionMode)
        engine = PermissionEngine(mode=PermissionMode.AUTO)
        engine.delegated_trust = 2  # även "autonom" delegering
        d = engine.evaluate('odoo_call_method', {'model': 'res.partner'})
        self.assertFalse(d.allowed,
                         'hårt stopp ska nekas i AUTO även för delegerat')

    def test_read_still_allowed_under_low_trust(self):
        from odoo.addons.ai_agent_core.core.permission import (
            PermissionEngine, PermissionMode)
        engine = PermissionEngine(mode=PermissionMode.AUTO)
        engine.delegated_trust = 0
        d = engine.evaluate('odoo_search', {'model': 'res.partner'})
        self.assertTrue(d.allowed)
        self.assertFalse(d.needs_user, 'läsning ska inte kräva godkännande')

    def test_apply_delegated_trust_reads_task(self):
        """_apply_delegated_trust sätter min(beställare, utförare)."""
        env, _ = self._requester_env(self.chief)
        task = env._delegate_task_to(self.worker, 'Skriv något')
        # worker kör uppdraget: beställare chief (steg 1), worker (steg 2).
        env2, sess = self._requester_env(self.worker, task=task)
        from odoo.addons.ai_agent_core.core.permission import (
            PermissionEngine, PermissionMode)
        loop = type('L', (), {})()
        loop.permissions = PermissionEngine(mode=PermissionMode.AUTO)
        env2._apply_delegated_trust(loop, sess)
        self.assertEqual(loop.permissions.delegated_trust, 1)

    def test_own_task_not_tightened(self):
        """Ett eget uppdrag (ingen förälder) påverkas inte."""
        env, sess = self._requester_env(self.worker)
        own = self.Task.create({
            'name': 'Eget', 'coworker_id': self.worker.id})
        sess.ai_task_id = own.id
        from odoo.addons.ai_agent_core.core.permission import (
            PermissionEngine, PermissionMode)
        loop = type('L', (), {})()
        loop.permissions = PermissionEngine(mode=PermissionMode.AUTO)
        env._apply_delegated_trust(loop, sess)
        self.assertIsNone(loop.permissions.delegated_trust)


@tagged('coworker_delegation')
class TestColleagueCatalog(DelegationTestBase):
    """6.1–6.2: kollegakatalogen."""

    def test_catalog_lists_delegatable_colleagues(self):
        catalog = self.chief._build_colleague_catalog()
        self.assertIn('Driftare-DELTEST', catalog)
        self.assertIn('Support-DELTEST', catalog)

    def test_catalog_excludes_non_delegatable(self):
        catalog = self.chief._build_colleague_catalog()
        self.assertNotIn('Redovisning-DELTEST', catalog)

    def test_catalog_empty_without_department(self):
        orphan = self.Coworker.create({
            'name': 'Utan avd', 'status': 'active',
            'orchestration_mode': 'single'})
        self.assertEqual(orphan._build_colleague_catalog(), '')

    def test_catalog_marks_unreceptive(self):
        self.worker.last_successful_run = False
        catalog = self.chief._build_colleague_catalog()
        self.assertIn('EJ mottagbar', catalog)


@tagged('coworker_delegation')
class TestDelegationTool(DelegationTestBase):
    """2.1, 2.6: verktyget registreras och kräver explicit tool_ids."""

    def test_builtin_tool_seeded(self):
        tool = self.env['ai.tool'].search([
            ('name', '=', 'delegate_task')], limit=1)
        self.assertTrue(tool, 'delegate_task ska seedas som ai.tool')
        self.assertEqual(tool.builtin_name, 'delegate_task')

    def test_tool_not_implicit(self):
        """En agent utan delegate_task i tool_ids får inget verktyg."""
        cw = self.Coworker.create({
            'name': 'Ingen verktyg-DELTEST', 'status': 'active',
            'orchestration_mode': 'single'})
        tools, _ = cw._session_tools()
        self.assertNotIn('delegate_task', tools.list())


@tagged('coworker_delegation')
class TestTaskAging(DelegationTestBase):
    """7.1–7.2: task-åldring och kostnadsattribution via trädet."""

    def test_cost_chain_summable(self):
        env, _ = self._requester_env(self.chief)
        parent = env._delegate_task_to(self.worker, 'Steg 1')
        env2, _ = self._requester_env(self.worker, task=parent)
        child = env2._delegate_task_to(self.chief, 'Steg 2')
        self.assertEqual(child.parent_task_id, parent)
        self.assertIn(child, parent.delegated_task_ids)

    def test_aging_reported(self):
        """_cron_measure_runs rapporterar gamla todo-uppdrag."""
        from datetime import timedelta
        task = self.Task.create({
            'name': 'Åldrat uppdrag-DELTEST',
            'coworker_id': self.worker.id, 'status': 'todo'})
        old = fields.Datetime.now() - timedelta(days=5)
        self.env.cr.execute(
            'UPDATE ai_org_task SET create_date=%s WHERE id=%s',
            (old, task.id))
        task.invalidate_recordset()
        res = self.Coworker._cron_measure_runs(window_hours=24)
        self.assertIn('stale_tasks', res)
        self.assertGreaterEqual(len(res['stale_tasks']), 1)


@tagged('coworker_delegation')
class TestDelegationScenario(DelegationTestBase):
    """7.3–7.4: OKR-kaskad + scenariot 'kolla loggarna'."""

    def test_goal_cascade_contributes(self):
        """Delegerat arbete med goal_id bidrar till målet vid incheckning."""
        goal = self.env['ai.org.goal'].create({
            'name': 'Driftmål-DELTEST', 'status': 'active',
            'coworker_id': self.worker.id})
        env, _ = self._requester_env(self.chief)
        task = env._delegate_task_to(self.worker, 'Kolla loggarna',
                                     goal_id=goal.id)
        self.assertEqual(task.goal_id, goal)
        # Checka in som klar → uppdraget kopplas till målet.
        task.action_checkin('Loggarna kontrollerade — inga fel.')
        self.assertEqual(task.status, 'done')
        self.assertEqual(task.goal_id, goal)

    def test_scenario_kolla_loggarna_end_to_end(self):
        """Redovisning delegerar till Drift; mottagaren kör via heartbeat.

        Bevisar grundfunderingen: en smal coworker kan få arbete utfört av
        en kollega utan att själv äga förmågan.
        """
        # 1. Redovisning får en Drift-kollega i SAMMA avdelning att delegera
        #    till (annars nekad — tvärs över avdelningar). Flytta en driftare
        #    till ekonomi för scenariot.
        driftare = self.Coworker.create({
            'name': 'Driftare-scen-DELTEST', 'status': 'active',
            'orchestration_mode': 'single',
            'department_id': self.dept_ekonomi.id,
            'heartbeat_enabled': True, 'hitl_threshold': 'autonomous',
            'chat_user_id': self.owner.id})
        driftare.last_successful_run = fields.Datetime.now()

        # 2. Redovisning delegerar "kolla loggarna".
        env, _ = self._requester_env(self.accountant)
        task = env._delegate_task_to(driftare, 'Kolla loggarna på gw0')
        self.assertEqual(task.coworker_id, driftare)
        self.assertEqual(task.status, 'todo')

        # 3. Mottagarens heartbeat plockar upp uppdraget (kö → session).
        driftare._heartbeat()
        session = self.Session.search([
            ('coworker_id', '=', driftare.id),
            ('ai_task_id', '=', task.id)], limit=1)
        self.assertTrue(session, 'heartbeat ska checka ut och köa uppdraget')
        self.assertEqual(task.delegation_depth, 1)

        # 4. Kedjan är spårbar: uppdraget bär beställarens agent.
        self.assertEqual(task.beställare_ref._name, 'ai.agent')
