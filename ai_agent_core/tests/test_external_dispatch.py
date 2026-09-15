# -*- coding: utf-8 -*-
"""Tester för dispatch av externa agenter (external-agent-runtime §2).

Testerna pinnar kontraktet, inte implementationen:

- `runtime=in_process` dispatchar INGEN process (oförändrat beteende)
- `runtime=external` startar `pi-agent --mode serve` med rätt argument
- api-nyckeln går via MILJÖN, aldrig via argv (argv syns i `ps`)
- ingen ny route införs — OpenAI-vägen är den enda kanalen
- en agent utan upplöst användare dispatchas INTE (aldrig systemuser)
"""

from unittest.mock import patch, MagicMock

from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase


class TestExternalDispatch(TransactionCase):

    def setUp(self):
        super().setUp()
        self.Agent = self.env['ai.agent']
        self.Coworker = self.env['ai.coworker']
        self.user = self.env['res.users'].create({
            'name': 'Dispatch Test User',
            'login': 'dispatch_test_user',
            'email': 'dispatch_test@example.com',
        })
        self.coworker = self.Coworker.create({'name': 'Dispatch-test-coworker'})

    def _external_agent(self, **kw):
        vals = {'name': 'Dispatch-test-agent', 'runtime': 'external'}
        vals.update(kw)
        return self.Agent.create(vals)

    # ── 2.4: in_process dispatchar ingenting ─────────────────────────

    def test_in_process_does_not_spawn(self):
        """En agent med in_process får ALDRIG en subprocess."""
        agent = self.Agent.create({'name': 'In-process-agent'})
        with patch('subprocess.Popen') as popen:
            result = agent._dispatch_external(
                self.coworker, None, self.user, prompt='hej')
        self.assertIsNone(result)
        popen.assert_not_called()

    # ── 2.1: extern dispatch startar processen ───────────────────────

    def test_external_spawns_pi_agent_serve(self):
        """Extern agent startar pi-agent i serve-läge på en ledig port."""
        agent = self._external_agent()
        fake_proc = MagicMock()
        fake_proc.pid = 4242
        fake_proc.poll.return_value = None

        with patch('subprocess.Popen', return_value=fake_proc) as popen, \
             patch('odoo.addons.ai_agent_core.core.runtime.wait_for_port', return_value=True) as wfp, \
             patch('odoo.addons.ai_agent_core.core.runtime.find_free_port', return_value=9107):
            result = agent._dispatch_external(
                self.coworker, None, self.user, prompt='undersök')

        popen.assert_called_once()
        argv = popen.call_args[0][0]
        self.assertIn('--mode', argv)
        self.assertEqual(argv[argv.index('--mode') + 1], 'serve')
        self.assertIn('--port', argv)
        self.assertEqual(argv[argv.index('--port') + 1], '9107')
        self.assertEqual(argv[argv.index('--coworker') + 1], str(self.coworker.id))
        self.assertEqual(argv[argv.index('--prompt') + 1], 'undersök')
        wfp.assert_called_once()
        self.assertEqual(result['pid'], 4242)
        self.assertEqual(result['port'], 9107)

    # ── 2.3: nyckeln via miljön, aldrig argv ─────────────────────────

    def test_api_key_goes_via_env_not_argv(self):
        """Api-nyckeln får inte synas i argv (argv syns i `ps`)."""
        agent = self._external_agent()
        fake_proc = MagicMock()
        fake_proc.pid = 4243
        fake_proc.poll.return_value = None

        with patch('subprocess.Popen', return_value=fake_proc) as popen, \
             patch('odoo.addons.ai_agent_core.core.runtime.wait_for_port', return_value=True), \
             patch('odoo.addons.ai_agent_core.core.runtime.find_free_port', return_value=9108):
            agent._dispatch_external(self.coworker, None, self.user)

        argv = popen.call_args[0][0]
        env = popen.call_args[1].get('env') or {}
        self.assertNotIn('--api-key', argv)
        self.assertFalse(any('--api-key' in str(a) for a in argv),
                         'api-nyckeln får inte passera argv')
        self.assertIn('PI_AGENT_API_KEY', env)
        self.assertTrue(env['PI_AGENT_API_KEY'])

    # ── 2.1: ingen ny route ──────────────────────────────────────────

    def test_no_new_agent_routes(self):
        """Endast OpenAI-vägen används — inga /pi/task eller /pi/callback."""
        agent = self._external_agent()
        fake_proc = MagicMock()
        fake_proc.pid = 4244
        fake_proc.poll.return_value = None
        with patch('subprocess.Popen', return_value=fake_proc) as popen, \
             patch('odoo.addons.ai_agent_core.core.runtime.wait_for_port', return_value=True), \
             patch('odoo.addons.ai_agent_core.core.runtime.find_free_port', return_value=9109):
            agent._dispatch_external(self.coworker, None, self.user)
        argv = ' '.join(str(a) for a in popen.call_args[0][0])
        self.assertNotIn('/pi/task', argv)
        self.assertNotIn('/pi/callback', argv)
        self.assertNotIn('--task', argv)
        self.assertNotIn('--callback', argv)

    # ── D5: användarkontext ──────────────────────────────────────────

    def test_dispatch_without_user_raises(self):
        """Utan upplöst användare dispatchas ingenting (aldrig systemuser)."""
        agent = self._external_agent()
        with patch('subprocess.Popen') as popen:
            with self.assertRaises(ValidationError):
                agent._dispatch_external(self.coworker, None, None)
        popen.assert_not_called()

    def test_dispatch_as_systemuser_raises(self):
        """systemuser är aldrig tillåtet för en automatisk körning."""
        agent = self._external_agent()
        root = self.env.ref('base.user_root')
        with patch('subprocess.Popen') as popen:
            with self.assertRaises(ValidationError):
                agent._dispatch_external(self.coworker, None, root)
        popen.assert_not_called()


class TestDispatchUserResolution(TransactionCase):
    """Uppslagning av res.users FÖRE dispatch (external-agent-runtime §3)."""

    def setUp(self):
        super().setUp()
        self.Coworker = self.env['ai.coworker']
        self.operator = self.env['res.users'].create({
            'name': 'Operator User',
            'login': 'dispatch_operator',
            'email': 'operator@example.com',
        })
        self.configured = self.env['res.users'].create({
            'name': 'Configured Cron User',
            'login': 'dispatch_cron_user',
            'email': 'cron@example.com',
        })

    def test_interactive_uses_env_uid(self):
        """Knapp/server action → den som tryckte (env.uid)."""
        coworker = self.Coworker.create({'name': 'Interactive-coworker'})
        user = coworker.with_user(self.operator)._resolve_dispatch_user(
            init_type='server_action')
        self.assertEqual(user.id, self.operator.id)

    def test_cron_uses_configured_user(self):
        """Cron → konfigurerad användare, inte den som råkade köra jobbet."""
        coworker = self.Coworker.create({
            'name': 'Cron-coworker',
            'chat_user_id': self.configured.id,
        })
        user = coworker.with_user(self.operator)._resolve_dispatch_user(
            init_type='cron')
        self.assertEqual(user.id, self.configured.id)
        self.assertNotEqual(user.id, self.operator.id)

    def test_webhook_uses_configured_user(self):
        coworker = self.Coworker.create({
            'name': 'Webhook-coworker',
            'chat_user_id': self.configured.id,
        })
        user = coworker._resolve_dispatch_user(init_type='webhook')
        self.assertEqual(user.id, self.configured.id)

    def test_no_user_raises(self):
        """Ingen konfigurerad användare → högljutt fel, inte systemuser."""
        coworker = self.Coworker.create({'name': 'No-user-coworker'})
        with self.assertRaises(ValidationError):
            coworker._resolve_dispatch_user(init_type='cron')

    def test_never_returns_systemuser(self):
        """Ingen körningsväg får leda till systemuser."""
        root = self.env.ref('base.user_root')
        coworker = self.Coworker.create({
            'name': 'Systemuser-coworker',
            'chat_user_id': root.id,
        })
        with self.assertRaises(ValidationError):
            coworker._resolve_dispatch_user(init_type='cron')
        # Interaktiv väg med root som env.user ska också vägras.
        with self.assertRaises(ValidationError):
            coworker.with_user(root)._resolve_dispatch_user(
                init_type='server_action')


class TestExternalLifecycle(TransactionCase):
    """Livscykel för externa processer (external-agent-runtime §4)."""

    def setUp(self):
        super().setUp()
        self.Session = self.env['ai.coworker.session']
        from odoo.addons.ai_agent_core.core import runtime as rt
        self.rt = rt

    def _spawn_sleeper(self, seconds=60):
        """Starta en riktig sovande process (inte pi-agent) för livscykeltest."""
        import subprocess
        return subprocess.Popen(['sleep', str(seconds)])

    def test_abort_kills_process(self):
        """Abort dödar processen — SIGTERM räcker för en snäll process."""
        proc = self._spawn_sleeper()
        self.assertTrue(self.rt.pid_alive(proc.pid))
        gone = self.rt.kill_pid(proc.pid, grace=2.0)
        self.assertTrue(gone)
        self.assertFalse(self.rt.pid_alive(proc.pid))

    def test_abort_escalates_to_sigkill(self):
        """En process som ignorerar SIGTERM dödas med SIGKILL."""
        import subprocess
        # `sh` som fångar SIGTERM och sover vidare → tvingar SIGKILL-vägen.
        proc = subprocess.Popen(
            ['sh', '-c', 'trap "" TERM; sleep 60'])
        import time
        time.sleep(0.4)
        self.assertTrue(self.rt.pid_alive(proc.pid))
        gone = self.rt.kill_pid(proc.pid, grace=0.5)
        self.assertTrue(gone, 'SIGKILL-vägen ska ha dödat processen')
        self.assertFalse(self.rt.pid_alive(proc.pid))

    def test_dead_process_detected_and_session_marked_error(self):
        """Död process upptäcks av livs-heartbeatet; sessionen markeras error."""
        from odoo import fields
        proc = self._spawn_sleeper(seconds=60)
        pid = proc.pid
        session = self.Session.create({
            'status': 'active',
            'external_pid': pid,
            'external_started_at': fields.Datetime.now(),
        })
        proc.kill()
        proc.wait()
        result = self.Session._cron_reap_external()
        session.invalidate_recordset()
        self.assertEqual(session.status, 'error')
        self.assertIn('avslutades oväntat', session.error_detail or '')
        self.assertGreaterEqual(result['detected'], 1)

    def test_orphan_process_is_reaped(self):
        """Process som lever men vars session är stängd → föräldralös, dödas."""
        proc = self._spawn_sleeper(seconds=60)
        session = self.Session.create({
            'status': 'done',
            'external_pid': proc.pid,
        })
        result = self.Session._cron_reap_external()
        self.assertFalse(self.rt.pid_alive(proc.pid))
        self.assertGreaterEqual(result['reaped'], 1)

    def test_live_count_counts_living_processes(self):
        """Räknaren för levande externa agenter (D10-mätpunkten)."""
        proc = self._spawn_sleeper(seconds=60)
        self.Session.create({'status': 'active', 'external_pid': proc.pid})
        self.assertGreaterEqual(self.Session._live_external_count(), 1)
        proc.kill()
        proc.wait()
        self.assertEqual(self.Session._live_external_count(), 0)


class TestRuntimeProfileAndMeasurement(TransactionCase):
    """Runtime-profil och mätpunkt (external-agent-runtime §5)."""

    def setUp(self):
        super().setUp()
        self.Session = self.env['ai.coworker.session']
        from odoo.addons.ai_agent_core.core import runtime as rt
        self.rt = rt

    def test_session_records_resource_profile(self):
        """Sessionsraden bär resursprofilen efter en extern körning (D10)."""
        from odoo import fields
        session = self.Session.create({'status': 'active'})
        session._record_external_run({
            'pid': 12345, 'port': 9100, 'spawn_time': 0.244, 'rss_kb': 39321,
        })
        session.invalidate_recordset()
        self.assertEqual(session.external_pid, 12345)
        self.assertEqual(session.external_port, 9100)
        self.assertAlmostEqual(session.external_spawn_time, 0.244, places=3)
        self.assertEqual(session.external_rss_kb, 39321)
        self.assertTrue(session.external_started_at)

    def test_finalize_writes_duration(self):
        """Varaktigheten skrivs när körningen avslutas."""
        from odoo import fields
        session = self.Session.create({
            'status': 'active',
            'external_pid': 12345,
            'external_started_at': fields.Datetime.subtract(
                fields.Datetime.now(), seconds=12),
        })
        vals = session._finalize_external_run(rss_kb=40000)
        self.assertGreaterEqual(vals['external_duration'], 11.0)
        self.assertEqual(vals['external_rss_kb'], 40000)

    def test_no_hardcoded_concurrency_ceiling(self):
        """Inget fast samtidighetstak är hårdkodat (D10) — pinna avsikten."""
        import inspect
        src = inspect.getsource(
            self.env['ai.coworker.session']._cron_reap_external)
        # Ingen jämförelse mot ett konstant antal agenter.
        for forbidden in ('MAX_AGENTS', 'MAX_EXTERNAL', 'CONCURRENCY_LIMIT'):
            self.assertNotIn(forbidden, src)
        # Räknaren finns, men jämförs inte mot ett tak.
        count_src = inspect.getsource(
            self.env['ai.coworker.session']._live_external_count)
        self.assertNotIn('>=', count_src)
        self.assertNotIn('>', count_src)

    def test_live_count_is_exposed_on_agent(self):
        """Räknaren för levande externa agenter exponeras på ai.agent."""
        agent = self.env['ai.agent'].create({'name': 'Counter-agent'})
        self.assertIsInstance(agent.live_external_count, int)
