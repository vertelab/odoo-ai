# -*- coding: utf-8 -*-
"""Tester för att en schemalagd quest-korning BÄR sitt haveri.

VARFÖR DESSA TESTER FINNS: `action_run_scheduled` skapade en session, och
kraschade sedan på ett importfel i `core/provider.py` (`from tenacity import`).
Undantaget fångades och kvittensen skrevs — men sessionen lämnades orörd som
'active'. Idle-cronen stängde den efteråt som 'done'/'idle', alltså
"färdigpratad". Resultatet i drift: 60 sessioner, 0 meddelanden, 0 tokens —
tomma sessioner som såg ut som avslutade konversationer.

Samma kod hade också en andra defekt: den LYCKADE vägen skrev
`session.write({'result': ...})`, men fältet `result` finns inte på
`ai.coworker.session` (bara på `ai.mail.test.wizard`). Det gav ValueError,
som fångades av samma except-gren — så en lyckad körning rapporterades som
`last_status='error'`. I drift hade questen 13 körningar och noll 'ok'.

Därför testar vi BETEENDET: att en session som havererar i setup blir
'error'/'setup_failed' och får ett system-meddelande — inte 'done'/'idle'.
"""

from unittest.mock import patch

from odoo.tests import common, tagged

import logging
_logger = logging.getLogger(__name__)


@tagged('okf', 'session', 'post_install', '-at_install')
class TestScheduledRunFailure(common.TransactionCase):
    """En havererad schemalagd körning ska synas i data."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Coworker = cls.env['ai.coworker']
        cls.Session = cls.env['ai.coworker.session']
        cls.Line = cls.env['ai.coworker.session.line']

    def _make_quest(self, name='test-quest'):
        return self.Coworker.create({
            'name': name,
            'description': 'Gör något schemalagt.',
        })

    def _sessions_for(self, quest):
        return self.Session.search([('coworker_id', '=', quest.id)])

    def test_setup_failure_marks_session_error_not_done(self):
        """Kraschar setup: sessionen blir 'error'/'setup_failed', inte 'active'."""
        quest = self._make_quest('test-setup-failure')

        with patch(
            'odoo.addons.ai_agent_core.core.provider.ProviderFactory.from_coworker',
            side_effect=ModuleNotFoundError("No module named 'tenacity'"),
        ):
            result = quest.action_run_scheduled()

        self.assertEqual(result.get('status'), 'error')

        sessions = self._sessions_for(quest)
        self.assertEqual(len(sessions), 1,
                         'exakt en session ska ha skapats')
        session = sessions[0]

        # KÄRNAN: haveriet syns i sessionen.
        self.assertEqual(session.status, 'error',
                         "sessionen får inte lämnas som 'active'")
        self.assertEqual(session.finish_reason, 'setup_failed')
        self.assertTrue(session.end_date,
                        'en stängd session ska ha ett slutdatum')

    def test_setup_failure_leaves_a_message(self):
        """Haveriet ska lämna ett spår — inte en tyst tom session."""
        quest = self._make_quest('test-setup-message')

        with patch(
            'odoo.addons.ai_agent_core.core.provider.ProviderFactory.from_coworker',
            side_effect=ModuleNotFoundError("No module named 'tenacity'"),
        ):
            quest.action_run_scheduled()

        session = self._sessions_for(quest)[:1]
        lines = self.Line.search([('session_id', '=', session.id)])

        self.assertEqual(len(lines), 1,
                         'haveriet ska lämna ett meddelande, inte tystnad')
        self.assertEqual(lines[0].role, 'system')
        self.assertIn('tenacity', lines[0].content,
                      'felmeddelandet ska gå att läsa i sessionen')

    def test_scheduled_session_records_init_type_cron(self):
        """Sessionen ska bära sitt ursprung — det var null för alla 60."""
        quest = self._make_quest('test-init-type')

        with patch(
            'odoo.addons.ai_agent_core.core.provider.ProviderFactory.from_coworker',
            side_effect=ModuleNotFoundError("No module named 'tenacity'"),
        ):
            quest.action_run_scheduled()

        session = self._sessions_for(quest)[:1]
        self.assertEqual(session.init_type, 'cron')

    def test_quest_stats_record_the_failure(self):
        """Kvittensen ska räkna körningen och sätta last_status='error'."""
        quest = self._make_quest('test-stats')
        before = quest.run_count

        with patch(
            'odoo.addons.ai_agent_core.core.provider.ProviderFactory.from_coworker',
            side_effect=ModuleNotFoundError("No module named 'tenacity'"),
        ):
            quest.action_run_scheduled()

        self.assertEqual(quest.run_count, before + 1)
        self.assertEqual(quest.last_status, 'error')
        self.assertTrue(quest.last_run)

    def test_result_field_does_not_exist_on_session(self):
        """Regressionvakt: 'result' finns inte på ai.coworker.session.

        Den gamla koden skrev `session.write({'result': ...})` på den LYCKADE
        vägen. Fältet existerar inte → ValueError → fångades av except-grenen
        → lyckade körningar rapporterades som 'error'. Testet pinnar att
        fältet inte finns, så att ingen återinför det utan att bygga det.
        """
        self.assertNotIn(
            'result', self.Session._fields,
            "om 'result' byggs på ai.coworker.session, uppdatera "
            "action_run_scheduled att skriva det igen — och ta bort denna vakt")
