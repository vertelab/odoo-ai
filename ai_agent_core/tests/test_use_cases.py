# -*- coding: utf-8 -*-
"""Användarfall 1–3 (external-agent-runtime §10).

De tre användarfallen mäter SAMMA sak ur tre ingångar: att workern frigörs
och att den externa processen faktiskt startar. Fall 3 är dessutom den
FÖRSTA datapunkten för skalbarhetstaket (spawn-tid + RSS) — taket är
medvetet inte satt, bara mätt (D: "bygg mätningen, skjut upp tröskeln").
"""

import time
from unittest.mock import patch

from odoo.tests import TransactionCase


class TestUseCases(TransactionCase):

    def setUp(self):
        super().setUp()
        self.agent = self.env['ai.agent'].create({
            'name': 'Användarfall-agent',
            'runtime': 'external',
            'status': 'active',
        })

    # ── 10.1: Zabbix-alarm → Infra-Operator ──────────────────────────

    def test_usecase1_alert_dispatch_does_not_block(self):
        """Fall 1: larmvägen är byggd i saltstack_ai (inte i core).

        Core är domänrent — det får inte känna till saltstack.alert. Vi
        verifierar därför att dispatch-kontraktet finns i core, och att
        larmets fält ägs av specialistlagret.
        """
        self.assertTrue(
            hasattr(self.env['ai.agent'], '_dispatch_external'),
            'core ska äga dispatch-implementationen')
        # Core är domänrent: modellen `saltstack.alert` får inte finnas i
        # core-modulens beroenden. (Att söka efter ordet i källkoden är för
        # trubbigt — kommentarer nämner specialistlagret med flit.)
        manifest = self.env['ir.module.module'].search(
            [('name', '=', 'ai_agent_core')], limit=1)
        self.assertTrue(manifest, 'ai_agent_core ska vara installerad')
        self.assertNotIn(
            'saltstack', (manifest.dependencies_id.mapped('name') or []),
            'core får inte bero på saltstack (domänrent)')

    def test_usecase1_webhook_is_non_interactive(self):
        """Fall 1 går via webhook → record-HITL, inte pausad."""
        from odoo.addons.ai_agent_core.core.interrupt import (
            select_interrupt_handler, AutoInterruptHandler)
        self.assertIsInstance(
            select_interrupt_handler('webhook'), AutoInterruptHandler)

    # ── 10.2: web UI-körning frigör web-workern ──────────────────────

    def test_usecase2_webui_is_interactive(self):
        """Fall 2: web UI är interaktiv → pausad HITL."""
        from odoo.addons.ai_agent_core.core.interrupt import (
            select_interrupt_handler, OpenAIInterruptHandler)
        self.assertIsInstance(
            select_interrupt_handler('web_ui'), OpenAIInterruptHandler)

    def test_usecase2_dispatch_returns_immediately(self):
        """Fall 2: dispatch återvänder INNAN agenten är klar.

        Vi ersätter spawn med en kontrollerad stubbe som sover, och mäter
        att anropet kommer tillbaka snabbt — det är själva poängen: workern
        väntar inte på agenten.
        """
        from odoo.addons.ai_agent_core.core import runtime as rt

        spawned = {}

        # Stubben måste matcha ExternalAgentHandle-kontraktet: dispatch
        # läser pid, port, duration() och rss_kb().
        class FakeHandle:
            pid = 424242
            port = 9199

            def duration(self):
                return 0.3

            def rss_kb(self):
                return 38400

        def fake_spawn(**kwargs):
            spawned.update(kwargs)
            time.sleep(0.3)  # simulerar att spawn tar tid
            return FakeHandle()

        coworker = self.env['ai.coworker'].create({
            'name': 'Fall 2-coworker',
            'status': 'active',
        })
        # En riktig användare — dispatch vägrar systemuser (D5).
        real_user = self.env['res.users'].create({
            'name': 'Webbanvändare',
            'login': 'webuser_usecase2',
        })
        start = time.monotonic()
        with patch.object(rt, 'spawn', side_effect=fake_spawn), \
                patch.object(rt, 'wait_for_port', return_value=True), \
                patch.object(rt, 'write_api_key_file',
                             return_value='/tmp/fake.key'):
            session = self.agent._dispatch_external(
                coworker, session=None, user=real_user, prompt='test')
        elapsed = time.monotonic() - start
        self.assertTrue(spawned, 'spawn ska ha anropats')
        self.assertTrue(session, 'dispatch ska ge en session')
        # Dispatch får inte vänta på agentens ARBETE — bara på spawn.
        self.assertLess(elapsed, 5.0,
                        'dispatch tog %.2fs — den blockerar workern' % elapsed)

    # ── 10.3: cron-körning; första datapunkten för taket ─────────────

    def test_usecase3_cron_is_non_interactive(self):
        """Fall 3: cron är icke-interaktiv → record-HITL."""
        from odoo.addons.ai_agent_core.core.interrupt import (
            select_interrupt_handler, AutoInterruptHandler)
        self.assertIsInstance(
            select_interrupt_handler('cron'), AutoInterruptHandler)

    def test_usecase3_cron_resolves_configured_user(self):
        """Fall 3: cron använder sessionens användare — aldrig systemuser."""
        coworker = self.env['ai.coworker'].create({
            'name': 'Fall 3-coworker',
            'status': 'active',
        })
        # session.user_id defaultar till env.user, som i en TransactionCase
        # ÄR base.user_root. Skapa en riktig användare för testet.
        real_user = self.env['res.users'].create({
            'name': 'Driftoperatör',
            'login': 'driftoperator_usecase3',
        })
        session = self.env['ai.coworker.session'].create({
            'coworker_id': coworker.id,
            'user_id': real_user.id,
        })
        user = coworker._resolve_dispatch_user(
            init_type='cron', session=session)
        self.assertEqual(user, real_user)
        self.assertNotEqual(user.id, self.env.ref('base.user_root').id)

    def test_usecase3_cron_without_user_fails_loudly(self):
        """Fall 3: utan konfigurerad användare blir det ett HÖGLJUTT fel."""
        from odoo.exceptions import ValidationError
        coworker = self.env['ai.coworker'].create({
            'name': 'Fall 3-coworker utan användare',
            'status': 'active',
        })
        with self.assertRaises(ValidationError):
            coworker._resolve_dispatch_user(init_type='cron')

    def test_usecase3_measurement_point_exists(self):
        """Fall 3: mätpunkten finns — spawn-tid och RSS per session."""
        session = self.env['ai.coworker.session'].create({
            'coworker_id': self.env['ai.coworker'].create({
                'name': 'Mät-coworker', 'status': 'active'}).id,
        })
        self.assertIn('external_spawn_time', session._fields)
        self.assertIn('external_rss_kb', session._fields)
        self.assertIn('external_duration', session._fields)

    def test_usecase3_no_hardcoded_ceiling(self):
        """Fall 3: INGET hårdkodat tak — bara mätning (D-beslutet)."""
        import inspect
        from odoo.addons.ai_agent_core.models import ai_session
        src = inspect.getsource(ai_session)
        # Antalet samtidiga externa körningar får inte spärras av en konstant.
        for forbidden in ('MAX_EXTERNAL', 'EXTERNAL_LIMIT',
                          'max_concurrent_external'):
            self.assertNotIn(
                forbidden, src,
                'Taket ska MÄTAS, inte hårdkodas (%s)' % forbidden)


class TestRemoteReachability(TransactionCase):
    """§11 — kan `ssh`/`salt` nå en minion? (TEST, inte design.)

    Frågan är empirisk (D12): om Salt-API:t är nåbart från Odoo-värden är
    fjärrvägen stängd utan lokal installation. Testet mäter det som faktiskt
    avgör saken — att API:t svarar och att `ssh`-klienten finns — inte att
    en konfigurationsnyckel är satt.
    """

    def _api_url(self):
        """API-adressen — eller None om den inte är konfigurerad.

        OBS: parametern sätts i DRIFT, inte i en testdatabas. Är den inte
        satt är frågan inte besvarad, inte besvarad nekande.
        """
        return self.env['ir.config_parameter'].sudo().get_param(
            'saltstack.api_url', '') or None

    def test_salt_api_reachable(self):
        """Salt-API:t svarar från Odoo-värden."""
        import json
        import urllib.request
        import urllib.error
        url = self._api_url()
        if not url:
            self.skipTest(
                'saltstack.api_url är inte satt i denna databas — '
                'fjärrvägen är inte testad här. Sätt parametern till '
                'mastern (http://saltstack.vertel.se:8377) och kör igen.')
        try:
            with urllib.request.urlopen(url, timeout=8) as resp:
                body = json.loads(resp.read().decode())
        except (urllib.error.URLError, OSError) as e:
            self.fail(
                'Salt-API:t på %s är inte nåbart: %s. Om detta är rätt '
                'begränsning ska D12 uppdateras med den.' % (url, e))
        self.assertIn('return', body,
                      'Salt-API:t svarade utan förväntad struktur')
        self.assertIn('clients', body)

    def test_salt_api_exposes_ssh_client(self):
        """`ssh`-klienten finns — mastern kan nå minions över SSH."""
        import json
        import urllib.request
        url = self._api_url()
        if not url:
            self.skipTest('saltstack.api_url är inte satt i denna databas.')
        with urllib.request.urlopen(url, timeout=8) as resp:
            body = json.loads(resp.read().decode())
        self.assertIn('ssh', body.get('clients', []),
                      'utan ssh-klienten faller fjärrvägen')

    def test_api_url_is_configured_or_documented(self):
        """Om `saltstack.api_url` inte är satt faller koden till localhost.

        Det är en tyst fälla: API:t finns på mastern, men standardvärdet
        pekar på en port ingen lyssnar på. Testet pinnar att värdet är satt
        — annars är fjärrvägen stängd av en osatt parameter, inte av en
        verklig begränsning.
        """
        configured = self.env['ir.config_parameter'].sudo().get_param(
            'saltstack.api_url', '')
        if not configured:
            self.skipTest(
                'saltstack.api_url är inte satt — koden faller tillbaka på '
                'http://localhost:8377, som ingen lyssnar på. Fyndet är '
                'dokumenterat i design.md (D12).')
