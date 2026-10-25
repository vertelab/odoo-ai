# -*- coding: utf-8 -*-
"""Tester för schemalagt-arbete — tidsatta uppgifter och heartbeat-dispatchern.

Täcker tasks 1.1–1.5 (scheduled_at), 2.1–2.8 (dispatchern: delad pool, två
tak), 3.x (cron.task i stället för ir.cron per coworker), 4.x (trådfrigöring),
5.x (mottagbarhetsbesked + behörighetsstyrd aktivering) och 7.x (onboarding).

KÄRNAN i ändringen: heartbeat är en `cron.task` (cadence min) som körs av
cron_tick-motorn — INTE en ir.cron per coworker. Uppgiftslistan är en DELAD
pool och uppgifter delas ut en i taget, med två tak (antal + tid).
"""

from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import ValidationError
from odoo.tests.common import TransactionCase, tagged

from ._config_param_guard import ConfigParamGuardedCase


class HeartbeatBase(ConfigParamGuardedCase):
    """Gemensam uppsättning: coworker med ägare, agent, provider."""

    def setUp(self):
        super().setUp()
        self.Coworker = self.env['ai.coworker']
        self.Task = self.env['ai.org.task']
        self.Session = self.env['ai.coworker.session']

        n = self.env['res.users'].search_count([])
        self.owner = self.env['res.users'].create({
            'name': 'HB Owner %s' % n,
            'login': 'hb_owner_%s' % n,
            'password': 'x',
            'groups_id': [(6, 0, [self.env.ref('base.group_user').id])],
        })
        self.provider = self.env['ai.provider'].create({
            'name': 'HB Provider %s' % n, 'provider_type': 'custom',
            'status': 'confirmed'})
        self.model = self.env['ai.model'].create({
            'name': 'hb-model-%s' % n, 'provider': self.provider.id})

    def _cw(self, name, **kw):
        vals = {
            'name': name,
            'status': 'active',
            'orchestration_mode': 'single',
            'heartbeat_enabled': True,
            'chat_user_id': self.owner.id,
        }
        vals.update(kw)
        cw = self.Coworker.create(vals)
        agent = self.env['ai.agent'].create({
            'name': 'Agent %s' % name, 'model_id': self.model.id})
        self.env['ai.coworker.agent'].create({
            'coworker_id': cw.id, 'agent_id': agent.id})
        return cw

    def _task(self, coworker, name='Uppgift', **kw):
        vals = {
            'name': name,
            'coworker_id': coworker.id,
            'status': 'todo',
            'description': 'Utför %s' % name,
        }
        vals.update(kw)
        return self.Task.create(vals)


@tagged('schemalagt_arbete')
class TestScheduledAt(HeartbeatBase):
    """1.1–1.4: scheduled_at som tidigast-tid."""

    def test_field_exists_and_defaults_empty(self):
        cw = self._cw('SA-Coworker')
        task = self._task(cw)
        self.assertFalse(task.scheduled_at)
        self.assertIn('scheduled_at', task._fields)

    def test_future_task_not_dispatched(self):
        """1.2: framtidsatt uppgift checkas inte ut och får ingen session."""
        cw = self._cw('SA-Future')
        future = fields.Datetime.now() + timedelta(hours=3)
        task = self._task(cw, 'Framtid', scheduled_at=future)

        self.Coworker._heartbeat_dispatch()

        task.invalidate_recordset()
        self.assertEqual(task.status, 'todo')
        self.assertFalse(task.checkout_lock)
        self.assertFalse(self.Session.search([
            ('ai_task_id', '=', task.id)]))

    def test_due_task_is_dispatched(self):
        """1.3: förfallen scheduled_at plockas som en vanlig uppgift."""
        cw = self._cw('SA-Due')
        past = fields.Datetime.now() - timedelta(hours=1)
        task = self._task(cw, 'Förfallen', scheduled_at=past)

        self.Coworker._heartbeat_dispatch()

        task.invalidate_recordset()
        self.assertEqual(task.status, 'in_progress')
        self.assertTrue(task.checkout_lock)

    def test_future_does_not_block_current(self):
        """1.3: framtidsatt uppgift blockerar inte en aktuell."""
        cw = self._cw('SA-Block')
        future = fields.Datetime.now() + timedelta(hours=3)
        self._task(cw, 'Framtid', scheduled_at=future)
        current = self._task(cw, 'Aktuell')

        self.Coworker._heartbeat_dispatch()

        current.invalidate_recordset()
        self.assertEqual(current.status, 'in_progress')

    def test_measure_runs_exempts_future_tasks(self):
        """1.4: mätaren friar framtidsatta uppgifter från åldring."""
        cw = self._cw('SA-Measure')
        future = fields.Datetime.now() + timedelta(days=30)
        task = self._task(cw, 'Väntar', scheduled_at=future)
        # Tvinga gammal create_date.
        self.env.cr.execute(
            "UPDATE ai_org_task SET create_date = now() - interval '10 days' "
            "WHERE id = %s", (task.id,))
        task.invalidate_recordset()

        result = self.Coworker._cron_measure_runs(window_hours=24)
        ids = [t['id'] for t in result['stale_tasks']]
        self.assertNotIn(task.id, ids)

    def test_measure_runs_still_flags_overdue_tasks(self):
        """1.4 (kontroll): en förfallen gammal uppgift flaggas fortfarande."""
        cw = self._cw('SA-Measure2')
        task = self._task(cw, 'Gammal')
        self.env.cr.execute(
            "UPDATE ai_org_task SET create_date = now() - interval '10 days' "
            "WHERE id = %s", (task.id,))
        task.invalidate_recordset()

        result = self.Coworker._cron_measure_runs(window_hours=24)
        ids = [t['id'] for t in result['stale_tasks']]
        self.assertIn(task.id, ids)


@tagged('schemalagt_arbete')
class TestDispatcher(HeartbeatBase):
    """2.1–2.8: delad pool, en i taget, två tak."""

    def test_dispatches_from_shared_pool(self):
        """2.1/2.2: uppgifter ur poolen delas ut till sin coworker."""
        cw_a = self._cw('Disp-A')
        cw_b = self._cw('Disp-B')
        t_a = self._task(cw_a, 'A-uppgift')
        t_b = self._task(cw_b, 'B-uppgift')

        result = self.Coworker._heartbeat_dispatch()

        self.assertEqual(result['dispatched'], 2)
        t_a.invalidate_recordset()
        t_b.invalidate_recordset()
        self.assertEqual(t_a.status, 'in_progress')
        self.assertEqual(t_b.status, 'in_progress')

    def test_session_queued_with_frozen_prompt(self):
        """2.2: sessionen får heartbeat_pending och fryst job_prompt."""
        cw = self._cw('Disp-Prompt')
        task = self._task(cw, 'Prompt-uppgift')

        self.Coworker._heartbeat_dispatch()

        session = self.Session.search([('ai_task_id', '=', task.id)])
        self.assertEqual(len(session), 1)
        self.assertTrue(session.heartbeat_pending)
        self.assertIn('Prompt-uppgift', session.job_prompt or '')

    def test_same_coworker_gets_multiple_tasks(self):
        """2.3: ingen 'ledig coworker'-spärr — flera sessioner är tillåtna."""
        cw = self._cw('Disp-Multi')
        self._task(cw, 'U1')
        self._task(cw, 'U2')

        result = self.Coworker._heartbeat_dispatch()

        self.assertEqual(result['dispatched'], 2)
        sessions = self.Session.search([('coworker_id', '=', cw.id)])
        self.assertEqual(len(sessions), 2)

    def test_max_tasks_cap(self):
        """2.4: antalstaket begränsar utdelningen."""
        cw = self._cw('Disp-Cap')
        for i in range(5):
            self._task(cw, 'Cap-%s' % i)

        result = self.Coworker._heartbeat_dispatch(max_tasks=2)

        self.assertEqual(result['dispatched'], 2)
        remaining = self.Task.search([
            ('coworker_id', '=', cw.id), ('status', '=', 'todo')])
        self.assertEqual(len(remaining), 3)

    def test_time_limit_stops_dispatch(self):
        """2.5: tidsgränsen stoppar utdelningen."""
        cw = self._cw('Disp-Time')
        for i in range(5):
            self._task(cw, 'Time-%s' % i)

        # max_seconds=0 → tiden har alltid passerat vid första kollen.
        result = self.Coworker._heartbeat_dispatch(max_seconds=0)

        self.assertEqual(result['dispatched'], 0)
        remaining = self.Task.search([
            ('coworker_id', '=', cw.id), ('status', '=', 'todo')])
        self.assertEqual(len(remaining), 5)

    def test_no_task_is_lost_across_ticks(self):
        """2.6: resten tas i nästa tick — ingen uppgift tappas."""
        cw = self._cw('Disp-Rest')
        for i in range(5):
            self._task(cw, 'Rest-%s' % i)

        first = self.Coworker._heartbeat_dispatch(max_tasks=2)
        # Frigör de utcheckade så nästa tick kan ta resten.
        for t in self.Task.search([('coworker_id', '=', cw.id)]):
            t.action_release()
        second = self.Coworker._heartbeat_dispatch(max_tasks=10)

        self.assertEqual(first['dispatched'], 2)
        self.assertEqual(second['dispatched'], 5)

    def test_non_operational_coworker_skipped(self):
        """2.7: icke-körbar coworker får ingen uppgift — den förblir todo."""
        cw = self._cw('Disp-Off', heartbeat_enabled=False)
        task = self._task(cw, 'Off-uppgift')

        result = self.Coworker._heartbeat_dispatch()

        # Databasen kan ha egna icke-operativa coworkers med köade uppgifter
        # — `skipped` räknar dem också. Det som prövas är att VÅR uppgift
        # inte delades ut.
        self.assertEqual(result['dispatched'], 0)
        self.assertGreaterEqual(result['skipped'], 1)
        task.invalidate_recordset()
        self.assertEqual(task.status, 'todo')
        self.assertFalse(task.checkout_lock)

    def test_empty_prompt_task_not_left_checked_out(self):
        """2.8: uppgift utan prompt lämnas inte utcheckad."""
        cw = self._cw('Disp-Empty')
        task = self.Task.create({
            'name': '', 'coworker_id': cw.id, 'status': 'todo',
        })

        self.Coworker._heartbeat_dispatch()

        task.invalidate_recordset()
        self.assertFalse(task.checkout_lock)
        self.assertEqual(task.status, 'todo')


@tagged('schemalagt_arbete')
class TestCronTaskRegistration(HeartbeatBase):
    """3.1–3.5: heartbeat är en cron.task, inte en ir.cron per coworker."""

    def test_waker_registered_as_cron_task(self):
        """3.1: väckaren finns i motorns register med cadence min."""
        waker = self.env['cron.task'].search([
            ('technical_name', '=', 'ai_heartbeat_dispatch')], limit=1)
        self.assertTrue(waker, 'Väckaren ska vara registrerad som cron.task')
        self.assertEqual(waker.cadence, 'min')
        self.assertEqual(waker.model_name, 'ai.coworker')
        self.assertEqual(waker.method_name, '_heartbeat_all')
        self.assertTrue(waker.active)

    def test_old_heartbeat_ir_cron_deactivated(self):
        """3.2: ingen dubbelväckning — den gamla väckaren finns inte längre.

        Den var en `ir.cron` med `active=False` för spårbarhetens skull.
        Sedan 18.0.2.0.0 får ingen `ir.cron` skrivas av en datafil: Odoo
        vägrar uppdatera en cron medan den kör, och det var precis vad som
        fällde uppgraderingen. Posten togs därför bort helt — det som
        bevisas här är att den inte kommit tillbaka.
        """
        cron = self.env.ref(
            'ai_agent_core.cron_ai_heartbeat', raise_if_not_found=False)
        self.assertFalse(
            cron, 'Den gamla ir.cron-väckaren ska vara borta, inte bara avstängd')

    def test_no_ir_cron_created_for_heartbeat_init_type(self):
        """3.3: att aktivera heartbeat-init-typen skapar ingen ir.cron."""
        cw = self._cw('Reg-NoCron')
        before = self.env['ir.cron'].search_count([])
        self.env['ai.coworker.init_type'].create({
            'coworker_id': cw.id, 'init_type': 'heartbeat', 'enabled': True})
        after = self.env['ir.cron'].search_count([])
        self.assertEqual(before, after)

    def test_waker_can_be_run_manually(self):
        """3.4: väckaren kan triggas manuellt via motorns action_run_now."""
        waker = self.env['cron.task'].search([
            ('technical_name', '=', 'ai_heartbeat_dispatch')], limit=1)
        cw = self._cw('Reg-Manual')
        task = self._task(cw, 'Manuell')

        waker.action_run_now()

        task.invalidate_recordset()
        self.assertEqual(task.status, 'in_progress')
        waker.invalidate_recordset()
        self.assertTrue(waker.last_run)

    def test_coworker_without_heartbeat_not_dispatched(self):
        """3.5: coworker utan heartbeat får inga uppgifter."""
        cw = self._cw('Reg-Off', heartbeat_enabled=False)
        self._task(cw, 'Ska inte köas')

        result = self.Coworker._heartbeat_dispatch()

        self.assertEqual(result['dispatched'], 0)


@tagged('schemalagt_arbete')
class TestThreadRelease(HeartbeatBase):
    """4.1–4.4: väckaren frigör tråden; väckare och processor delar inte arbete."""

    def test_waker_does_not_run_llm(self):
        """4.1: väckaren skapar sessioner men anropar ingen LLM."""
        cw = self._cw('Rel-NoLLM')
        self._task(cw, 'Ingen LLM')

        with patch.object(type(cw), 'run') as mock_run:
            self.Coworker._heartbeat_dispatch()
            mock_run.assert_not_called()

    def test_global_waker_is_the_engine_entrypoint(self):
        """4.2: _heartbeat_all är motorns ingång och delegerar till dispatchern."""
        cw = self._cw('Rel-Global')
        task = self._task(cw, 'Global')

        self.Coworker._heartbeat_all()

        task.invalidate_recordset()
        self.assertEqual(task.status, 'in_progress')

    def test_waker_and_processor_do_not_share_work(self):
        """4.3: väckaren köar, processorn kör."""
        cw = self._cw('Rel-Split')
        task = self._task(cw, 'Delad')

        self.Coworker._heartbeat_dispatch()

        session = self.Session.search([('ai_task_id', '=', task.id)])
        self.assertTrue(session.heartbeat_pending)
        cw.invalidate_recordset()
        self.assertEqual(cw.run_count, 0)

    def test_max_tasks_parameter_is_read(self):
        """4.4: antalstaket räknas i uppgifter (inte coworkers)."""
        self.env['ir.config_parameter'].sudo().set_param(
            'ai_agent_core.heartbeat_max_tasks_per_tick', '1')
        cw = self._cw('Rel-Param')
        self._task(cw, 'P1')
        self._task(cw, 'P2')

        result = self.Coworker._heartbeat_dispatch()

        self.assertEqual(result['dispatched'], 1)


@tagged('schemalagt_arbete')
class TestOperationalAndActivation(HeartbeatBase):
    """5.1–5.6: mottagbarhetsbesked och behörighetsstyrd aktivering."""

    def test_operational_when_all_conditions_met(self):
        """5.1: alla villkor uppfyllda → operativ."""
        cw = self._cw('Op-OK')
        ok, reason = cw._heartbeat_operational()
        self.assertTrue(ok, reason)

    def test_not_operational_when_disabled(self):
        """5.1: avstängd heartbeat → icke-operativ med konkret skäl."""
        cw = self._cw('Op-Off', heartbeat_enabled=False)
        ok, reason = cw._heartbeat_operational()
        self.assertFalse(ok)
        self.assertIn('avstängt', reason)

    def test_not_operational_without_owner(self):
        """5.1/5.3: ingen ägare → icke-operativ, ingen systemuser-fallback."""
        cw = self._cw('Op-NoOwner', chat_user_id=False)
        ok, reason = cw._heartbeat_operational()
        self.assertFalse(ok)
        self.assertIn('ägare', reason)

    def test_not_operational_when_budget_exhausted(self):
        """5.1: tömd budget → icke-operativ."""
        cw = self._cw('Op-Budget')
        cw.monthly_cap_mtokens = 1
        # Skapa en session line som spränger taket. `token_sys` är BERÄKNAT
        # (compute, store=True) från token_input/token_output × sys_multiplier
        # — att skriva token_sys direkt ignoreras. Sätt indatafälten.
        session = self.Session.create({
            'coworker_id': cw.id, 'user_id': self.owner.id,
            'status': 'active', 'init_type': 'heartbeat'})
        self.env['ai.coworker.session.line'].create({
            'session_id': session.id, 'role': 'assistant',
            'content': 'x', 'token_input': 1000000, 'token_output': 1000000,
            'sys_multiplier': 1.0})
        cw.invalidate_recordset()
        ok, reason = cw._heartbeat_operational()
        self.assertFalse(ok)
        self.assertIn('budget', reason.lower())

    def test_delegation_refused_with_concrete_reason(self):
        """5.2: köning hos icke-operativ coworker nekas med konkret skäl."""
        requester = self._cw('Op-Req')
        target = self._cw('Op-Target', heartbeat_enabled=False)
        ok, reason = target._heartbeat_operational()
        self.assertFalse(ok)
        self.assertIn('avstängt', reason)

    def test_enable_heartbeat_requires_write_access(self):
        """5.4: utan skrivrätt nekas aktiveringen.

        `has_access('write')` kontrollerar modell- OCH record-regler på
        `self`. Vi verifierar grenen genom att tvinga `has_access` till False
        — att bygga en användare som saknar skrivrätt på ai.coworker är
        skört eftersom ACL:er är additiva och base.group_user har breda
        rättigheter i testmiljön. Grenen är det som ska bevisas.
        """
        cw = self._cw('Op-NoWrite', heartbeat_enabled=False)
        with patch.object(type(self.env.user), 'has_access',
                          return_value=False):
            with self.assertRaises(ValidationError):
                cw.action_enable_heartbeat()
        cw.invalidate_recordset()
        self.assertFalse(cw.heartbeat_enabled)

    def test_enable_heartbeat_with_write_access(self):
        """5.4 (kontroll): med skrivrätt aktiveras heartbeat."""
        cw = self._cw('Op-YesWrite', heartbeat_enabled=False)
        self.assertTrue(cw.action_enable_heartbeat())
        cw.invalidate_recordset()
        self.assertTrue(cw.heartbeat_enabled)

    def test_enable_heartbeat_idempotent(self):
        """5.5: redan operativ heartbeat ändras inte."""
        cw = self._cw('Op-Idem')
        self.assertTrue(cw.heartbeat_enabled)
        self.assertTrue(cw.action_enable_heartbeat())
        self.assertTrue(cw.heartbeat_enabled)

    def test_operational_gives_no_status_warning(self):
        """5.6: operativ heartbeat ger ingen varning."""
        cw = self._cw('Op-NoWarn')
        cw.invalidate_recordset()
        self.assertTrue(cw.heartbeat_operational)
        self.assertNotIn('⚠', cw.heartbeat_status or '')


@tagged('schemalagt_arbete')
class TestOnboardingHeartbeat(HeartbeatBase):
    """7.1–7.2: onboarding skapar en operativ heartbeat."""

    def test_ensure_waker_is_idempotent(self):
        """7.2: att säkerställa väckaren två gånger ger en post."""
        Onboarding = self.env['ai.onboarding']
        w1 = Onboarding._ensure_heartbeat_waker()
        w2 = Onboarding._ensure_heartbeat_waker()
        self.assertTrue(w1)
        self.assertEqual(w1.id, w2.id)
        count = self.env['cron.task'].search_count([
            ('technical_name', '=', 'ai_heartbeat_dispatch')])
        self.assertEqual(count, 1)

    def test_ensure_waker_reactivates_inactive(self):
        """7.1: en inaktiverad väckare aktiveras igen (ingen dubblett)."""
        waker = self.env['cron.task'].with_context(
            active_test=False).search([
            ('technical_name', '=', 'ai_heartbeat_dispatch')], limit=1)
        waker.active = False
        Onboarding = self.env['ai.onboarding']
        result = Onboarding._ensure_heartbeat_waker()
        self.assertTrue(result.active)
        # Ingen dubblett skapades (technical_name är unikt).
        count = self.env['cron.task'].with_context(
            active_test=False).search_count([
            ('technical_name', '=', 'ai_heartbeat_dispatch')])
        self.assertEqual(count, 1)
