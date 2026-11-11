# -*- coding: utf-8 -*-
"""Sessionens körplats (`machine`) — session-machine.

VARFÖR (session-machine-and-filter): `init_type` säger *vilken yta* som
startade sessionen (`openai_api` = Pi, `web_ui` = Odoo), men inte *vilken
maskin*. När Pi kör på flera burkar (laptop, luke, gateway) gick det inte att
se vilken som ägde en session — och därmed inte att felsöka maskinbundna fel.

Dessa tester bevisar:
  1. lookup sätter maskinen som klienten skickar
  2. utan `machine` blir värdet `Odoo` (skapad inuti Odoo), aldrig tomt
  3. en resume skriver ALDRIG över ett satt värde
  4. ett tomt fält får fyllas av en ny tur
  5. värdet kapas till 64 tecken
  6. coworker-vägen (chat/completions) sätter maskinen i båda skapandena
  7. fältet är indexerat, så grupperingen per maskin är billig
"""

import uuid

from odoo.tests import common, tagged
from ._test_users import create_user


@tagged('session_machine', 'post_install', '-at_install')
class TestSessionMachine(common.TransactionCase):
    """`ai.coworker.session.machine` — satt vid skapande, aldrig överskrivet."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Session = cls.env['ai.coworker.session']
        cls.user = create_user(cls.env, name='Session Machine User', login='session_machine@example.com', email='session_machine@example.com')
        cls.coworker = cls.env['ai.coworker'].create({
            'name': 'Session Machine Coworker',
            'status': 'active',
        })

    def _pi_id(self, label):
        """Unikt pi_session_id per test — driftsdatabasen har redan UUID:n."""
        return '%s-%s' % (label, uuid.uuid4())

    def _lookup(self, pi_session_id, machine=None):
        kwargs = {} if machine is None else {'machine': machine}
        session, created = self.Session._lookup_or_create_pi_session(
            pi_session_id, prompt='machine test', **kwargs)
        return session, created

    # ── 1. Lookup sätter maskinen ───────────────────────────────────────

    def test_lookup_sets_machine(self):
        """Pi:s hostname i requesten blir sessionens `machine`."""
        session, created = self._lookup(self._pi_id('a'), machine='luke')
        self.assertTrue(created)
        self.assertEqual(session.machine, 'luke')

    def test_lookup_without_machine_defaults_to_odoo(self):
        """Utan maskin: `Odoo` — sessionen skapades inuti Odoo, aldrig tomt."""
        session, _created = self._lookup(self._pi_id('b'))
        self.assertEqual(session.machine, 'Odoo')

    def test_machine_is_truncated(self):
        """Maskinnamnet kapas till 64 tecken (fältets praktiska tak)."""
        session, _created = self._lookup(self._pi_id('c'),
                                         machine='x' * 100)
        self.assertEqual(len(session.machine), 64)

    # ── 2. Resume bevarar körplatsen ────────────────────────────────────

    def test_resume_does_not_overwrite(self):
        """En handover till en annan maskin rör inte ursprunget."""
        pi_session_id = self._pi_id('d')
        session, _created = self._lookup(pi_session_id, machine='luke')
        resumed, created = self._lookup(pi_session_id, machine='gw0')
        self.assertFalse(created)
        self.assertEqual(resumed.id, session.id, 'samma pi_session_id → samma session')
        self.assertEqual(resumed.machine, 'luke',
                         'ett satt värde skrivs aldrig över vid resume')

    def test_empty_machine_is_filled(self):
        """En session skapad före fältet fanns fylls av nästa tur."""
        pi_session_id = self._pi_id('e')
        session, _created = self._lookup(pi_session_id, machine='luke')
        session.write({'machine': False})
        self.assertFalse(session.machine)
        filled, created = self._lookup(pi_session_id, machine='gw0')
        self.assertFalse(created)
        self.assertEqual(filled.machine, 'gw0')
        self.assertEqual(filled.id, session.id, 'sessionen behåller sitt id')

    # ── 3. Coworker-vägen (chat/completions) ────────────────────────────

    def test_coworker_session_gets_machine(self):
        """Okänt pi_session_id i chat-vägen → ny session med maskinen."""
        session, created = self.Session._find_or_create_coworker_session(
            self.coworker.id, self.user.id,
            pi_session_id=self._pi_id('f'), machine='gw0')
        self.assertTrue(created)
        self.assertEqual(session.machine, 'gw0')

    def test_coworker_session_without_machine_defaults_to_odoo(self):
        """Lokal väg (web_ui/cron …) behöver inte komma ihåg maskinen."""
        session, _created = self.Session._find_or_create_coworker_session(
            self.coworker.id, self.user.id,
            pi_session_id=self._pi_id('g'))
        self.assertEqual(session.machine, 'Odoo')

    # ── 4. Fältets form ────────────────────────────────────────────────

    def test_machine_field_is_indexed(self):
        """Grupperingen `group_by: machine` ska vara billig."""
        field = self.Session._fields['machine']
        self.assertTrue(field.index, 'machine ska vara indexerat')
        self.assertTrue(field.readonly, 'maskinen sätts av systemet, inte av UI')
        self.assertFalse(field.copy, 'en duplicerad session ärver inte körplatsen')
