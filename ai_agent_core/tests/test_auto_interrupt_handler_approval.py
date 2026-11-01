# -*- coding: utf-8 -*-
"""B4/B6 — HITL-routing: AutoInterruptHandler godkänner allt vs. nekar.

Bakgrund:

- AutoInterruptHandler godkände alla destruktiva verktyg, vilket kringgick
  hitl-dispatch-routing (HITL-002). Noll anropare i drift.
- select_interrupt_handler väljer endast baserat på init_type; den
  icke-interaktiva vägen (`AutoInterruptHandler`) har ingen anropare.
- Cron-körningar med godkännandekrävande verktyg släpptes igenom, vilket
  är ett säkerhetsproblem (B4). Det är vad vi fixar här.

Detta test (11.1) är ett strukturtest: det kontrollerar det existerande
beteendet (buggen), inte en ändring av koden.
"""

import asyncio

from odoo.tests.common import TransactionCase, tagged


def _run(coro):
    """Kör en coroutine i en egen loop — approve_tool är async."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@tagged('ai_core', 'post_install', '-at_install')
class TestAutoInterruptHandlerApproval(TransactionCase):
    """11.1: AutoInterruptHandler utan hitl_request godkänner allt."""

    def test_auto_interrupt_denies_when_no_hitl_request(self):
        """B4: utan hitl_request finns ingen mottagare — neka (fail-closed).

        FÖRE rättningen returnerade `approve_tool` True här, vilket
        auto-godkände destruktiva verktyg i obevakade körningar.
        """
        from odoo.addons.ai_agent_core.core.interrupt import AutoInterruptHandler

        handler = AutoInterruptHandler(hitl_request=None)
        result = _run(handler.approve_tool("odoo_unlink", "write", {}))
        self.assertFalse(
            result,
            "AutoInterruptHandler utan hitl_request får inte godkänna "
            "destruktiva verktyg — ingen kan svara på ärendet")

    def test_auto_interrupt_denies_when_hitl_request_provided(self):
        """När hitl_request finns, nekar AutoInterruptHandler destruktiva verktyg."""
        from odoo.addons.ai_agent_core.core.interrupt import AutoInterruptHandler
        # En dummy callable som registrerar HITL-posten.
        calls = []
        def hitl_request(tool_name, risk_level, arguments):
            calls.append((tool_name, risk_level, arguments))

        handler = AutoInterruptHandler(hitl_request=hitl_request)
        result = _run(handler.approve_tool("odoo_unlink", "write", {}))
        self.assertFalse(result, "AutoInterruptHandler med hitl_request nekar destruktiva verktyg")
        self.assertEqual(len(calls), 1, "HITL-post bör skapas för destruktivt verktyg")
