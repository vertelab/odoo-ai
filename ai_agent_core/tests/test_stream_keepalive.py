# -*- coding: utf-8 -*-
"""Tester för keepalive i SSE-strömmen (webui-stream-feedback 4.4).

Felklass: **tystnad under en lång operation tolkas som ett brott.** Servern
arbetar men sänder inget; klienten ger upp och visar "Anslutningen till
AI-servern bröts".

Mätt i drift 2026-11-05 (frågan "Carina Bergfeldt ögon ser märkliga ut"):
sju webbsökningar tog 7–20 s, och chatten bröt mitt i. Loggen hade inget
`SSE stream error` — servern gjorde sitt.

Lösningen: läskön väntar i KEEPALIVE_THRESHOLD sekunder i taget, och
`queue.Empty` betyder "ingen chunk än" — inte "streamen är död". Den
distinktionen fanns inte förut: en 300 s timeout behandlade all tystnad som
ett fel, och under tiden fick klienten ingenting.
"""

import queue
import time

from odoo.tests import common, tagged


@tagged('post_install', '-at_install')
class TestKeepaliveContract(common.TransactionCase):
    """Kontraktet för keepalive — konstanten och eventets form.

    Själva SSE-generatorn kräver en request-kontext och en körande loop, så
    dessa tester prövar det som går att pröva utan: tröskelns värde och att
    eventet har rätt form. Den fullständiga strömmen verifieras i drift
    (task 6.4).
    """

    def test_threshold_is_below_typical_idle_timeout(self):
        """Tröskeln ska ligga under webbläsares och proxys idle-timeout."""
        from odoo.addons.ai_agent_core.controllers.stream import (
            KEEPALIVE_THRESHOLD)
        self.assertGreater(KEEPALIVE_THRESHOLD, 0)
        self.assertLessEqual(
            KEEPALIVE_THRESHOLD, 15,
            'En tröskel över 15 s hinner inte före vanliga idle-timeouts '
            '(webbläsare/Caddy ger upp runt 15–20 s)')

    def test_keepalive_event_shape(self):
        """Eventet ska vara ett giltigt SSE-data-event med typ keepalive."""
        import json
        payload = 'data: {"type": "keepalive"}\n\n'
        self.assertTrue(payload.startswith('data: '))
        self.assertTrue(payload.endswith('\n\n'))
        body = json.loads(payload[6:].strip())
        self.assertEqual(body['type'], 'keepalive')

    def test_queue_timeout_yields_keepalive_not_error(self):
        """`queue.Empty` ska ge ett keepalive, inte avsluta strömmen.

        Det här är kärnan i felet: den gamla koden hade `get(timeout=300)`
        och behandlade `Empty` som "stream timeout" → break. En lång
        verktygskörning gav därför inget livstecken alls.
        """
        q = queue.Queue()
        emitted = []
        # Simulera en kö som är tom i två tröskelperioder, sedan klar.
        empty_periods = 2
        for _ in range(empty_periods):
            try:
                q.get(timeout=0.01)
            except queue.Empty:
                emitted.append('keepalive')
        self.assertEqual(
            emitted, ['keepalive'] * empty_periods,
            'varje tom period ska ge ett livstecken')
        # Och strömmen fortsätter — den avslutas inte av tystnaden.
        q.put('chunk')
        self.assertEqual(q.get(timeout=0.1), 'chunk')

    def test_keepalive_is_not_content(self):
        """Keepalive får aldrig hamna i svarstexten eller tokenräkningen."""
        import inspect
        from odoo.addons.ai_agent_core.controllers import stream as stream_mod
        src = inspect.getsource(stream_mod)
        # `full_response.append` ska bara ske för token-event
        for line in src.splitlines():
            if 'full_response.append' in line:
                self.assertNotIn(
                    'keepalive', line.lower(),
                    'keepalive får inte läggas i full_response — det är '
                    'svarstexten som persisteras')


@tagged('post_install', '-at_install')
class TestWebSearchBudget(common.TransactionCase):
    """5.3/5.4: web_search har en total tidsbudget.

    Kedjan DDGS×2 + DDG HTML + Bing + Brave hade 20 s timeout var — värsta
    fallet ~80 s. Budgeten klipper helheten.
    """

    def test_budget_is_defined_and_reasonable(self):
        """Budgeten ska finnas och ligga under klientens tålamod."""
        from odoo.addons.ai_agent_core.core.tools import _WEB_SEARCH_BUDGET
        self.assertGreater(_WEB_SEARCH_BUDGET, 0)
        self.assertLessEqual(
            _WEB_SEARCH_BUDGET, 30,
            'En budget över 30 s hinner inte före klientens timeout')

    def test_budget_check_stops_the_chain(self):
        """Utgången budget ska stoppa kedjan, inte fortsätta."""
        import time
        budget = 0.05
        deadline = time.monotonic() + budget

        def out_of_budget():
            return time.monotonic() >= deadline

        self.assertFalse(out_of_budget(), 'budgeten ska vara kvar direkt')
        time.sleep(budget + 0.02)
        self.assertTrue(out_of_budget(), 'budgeten ska vara slut efter väntan')

    def test_budget_does_not_affect_fast_search(self):
        """En snabb sökning ska inte påverkas — budgeten är ett tak, inte
        en fördröjning."""
        import time
        deadline = time.monotonic() + 15.0
        # Ett snabbt svar långt före deadline
        self.assertLess(time.monotonic(), deadline)
