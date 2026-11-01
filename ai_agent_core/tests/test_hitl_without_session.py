# -*- coding: utf-8 -*-
"""B5 — HITL-record utan session: felet ska synas, inte tystna.

Bakgrund: `_resolve_session()` (models/ai_coworker_hitl.py) **raisar**
`ValidationError` när körningen saknar session i context. Det är korrekt —
en HITL-förfrågan utan session är herrelös (design D7), och att hitta på en
session vore att påstå ett arbete som inte finns.

Problemet är anropsplatsen: en körväg som saknade kontext fick ett
undantag som antingen tystnade i en `except` eller kraschade körningen utan
att säga VILKEN väg som saknade kontext. Felsökningen blev gissning.

Rättningen är inte att sluta raisa — det är att fånga på anropsplatsen och
logga körvägens namn, så felet blir lokaliserbart.
"""

from unittest.mock import patch

from odoo.exceptions import ValidationError
from odoo.tests.common import TransactionCase, tagged


@tagged('ai_core', 'post_install', '-at_install')
class TestHitlWithoutSession(TransactionCase):
    """12.1: utan sessionskontext kastas ValidationError."""

    def setUp(self):
        super().setUp()
        self.coworker = self.env['ai.coworker'].create({
            'name': 'B5 Coworker',
        })

    def test_resolve_session_raises_without_context(self):
        cw = self.coworker.with_context()  # ingen _ai_context_id
        with self.assertRaises(ValidationError) as ctx:
            cw._resolve_session()
        message = str(ctx.exception)
        self.assertIn('session', message.lower())
        self.assertIn('_ai_context_id', message,
                      'felet ska namnge vad som saknas')

    def test_request_hitl_raises_without_session(self):
        cw = self.coworker.with_context()
        with self.assertRaises(ValidationError):
            cw._request_hitl(
                action_type='tool_approval',
                summary='Test utan session',
                context={'tool_name': 'odoo_unlink'},
            )

    def test_request_hitl_works_with_context(self):
        """12.3: normal körning (med kontext) skapar posten."""
        session = self.env['ai.coworker.session'].create({
            'coworker_id': self.coworker.id,
            'name': 'B5 session',
            'status': 'active',
            'user_id': self.env.user.id,
        })
        hitl = self.coworker.with_context(
            _ai_context_model='ai.coworker.session',
            _ai_context_id=session.id,
        )._request_hitl(
            action_type='tool_approval',
            summary='Test med session',
            context={'tool_name': 'odoo_unlink',
                     'model': 'ai.coworker.session',
                     'res_id': session.id},
            session=session,
        )
        self.assertTrue(hitl)
        self.assertEqual(hitl.session_id.id, session.id)
        self.assertEqual(hitl.state, 'asked')


@tagged('ai_core', 'post_install', '-at_install')
class TestHitlFailureIsLogged(TransactionCase):
    """12.2: anropsplatsen ska logga VILKEN körväg som saknade kontext."""

    def setUp(self):
        super().setUp()
        self.coworker = self.env['ai.coworker'].create({
            'name': 'B5 Log Coworker',
        })

    def test_cron_hitl_request_logs_the_path(self):
        """Cron-vägens hitl_request får inte tystna — den ska logga."""
        import asyncio
        from odoo.addons.ai_agent_core.core.interrupt import (
            AutoInterruptHandler)

        # Simulera cron-vägens hitl_request när sessionen saknas: den ska
        # fånga ValidationError och logga, inte krascha loopen.
        calls = []

        def _cron_hitl_request(tool_name, risk_level, arguments):
            try:
                self.coworker.with_context()._request_hitl(
                    action_type='tool_approval',
                    summary='cron utan session',
                    context={'tool_name': tool_name},
                )
            except ValidationError:
                calls.append(tool_name)
                raise

        handler = AutoInterruptHandler(hitl_request=_cron_hitl_request)
        loop = asyncio.new_event_loop()
        try:
            with self.assertLogs(
                    'odoo.addons.ai_agent_core.core.interrupt',
                    level='WARNING') as captured:
                allowed = loop.run_until_complete(
                    handler.approve_tool('odoo_unlink', 'destructive', {}))
        finally:
            loop.close()

        self.assertFalse(allowed, 'verktyget ska nekas')
        self.assertEqual(calls, ['odoo_unlink'],
                         'hitl_request ska ha anropats och fångat felet')
        joined = '\n'.join(captured.output)
        self.assertIn('odoo_unlink', joined,
                      'loggen ska namnge verktyget som nekades')
