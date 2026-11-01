# -*- coding: utf-8 -*-
"""Tester för gateway-sessionsidentiteten (gateway-anropseffektivitet D1).

Verifierar att:
  (a) en ny session får ett stabilt `gateway_session_id` som SKILJER sig
      från `name` (visningsetiketten),
  (b) `gateway_session_id` överlever en lång prompt — det klipps inte och
      skrivs inte över när `name` sätts från prompt-texten,
  (c) två sessioner med olika långa prompter får olika gateway-id:n,
  (d) `x-bf-session-id`-headern bär gateway-id:t, inte prompt-texten,
  (e) en session utan gateway-id (skapad före D1) inte skickar en
      prompt-text som header (ingen falsk nyckel).

Bakgrund: `name` sätts till `prompt[:80]` (ai_session) medan headern
klipptes till `name[:64]` — två olika trunkeringar av samma sträng, som
aldrig matchar varandra. Korrelationen mot `bifrost.request.log.session_id`
gav därför alltid noll träffar och `bifrost_cost` förblev omätt.
"""

from odoo.tests import common


class TestGatewaySessionIdentity(common.TransactionCase):
    """Stabil sessionsidentitet för gateway-korrelation."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Session = cls.env['ai.coworker.session']
        cls.user = cls.env['res.users'].create({
            'name': 'GW Identity User',
            'login': 'gw_identity@example.com',
            'email': 'gw_identity@example.com',
        })
        cls.coworker = cls.env['ai.coworker'].create({
            'name': 'GW Identity Coworker',
            'description': 'Gateway identity test',
            'status': 'active',
        })

    def test_new_session_gets_stable_identity(self):
        """(a) Ny session har ett gateway-id skilt från name."""
        sess = self.Session.create({
            'coworker_id': self.coworker.id,
            'status': 'active',
            'name': 'Kort prompt',
        })
        self.assertTrue(sess.gateway_session_id,
                        'Ny session ska ha ett gateway_session_id')
        self.assertNotEqual(sess.gateway_session_id, sess.name,
                            'Gateway-id får inte vara samma som name')

    def test_identity_survives_long_prompt(self):
        """(b) Lång prompt trunkerar name men rör inte gateway-id:t."""
        long_prompt = ('Jag har en Kona elbil som servas hos bilbyer i '
                       'Linköping ingår vägassistans i service avgiften '
                       'och jag undrar över villkoren för detta')
        sess = self.Session.create({
            'coworker_id': self.coworker.id,
            'status': 'active',
            'name': long_prompt[:80],
        })
        gw_id = sess.gateway_session_id
        self.assertTrue(gw_id)
        # name är trunkerad prompt, gateway-id är ett uuid — aldrig lika.
        self.assertNotEqual(gw_id, sess.name)
        self.assertNotIn(gw_id, sess.name)

    def test_two_sessions_different_prompts_differ(self):
        """(c) Två sessioner får olika gateway-id:n."""
        a = self.Session.create({
            'coworker_id': self.coworker.id, 'status': 'active',
            'name': 'Prompt A',
        })
        b = self.Session.create({
            'coworker_id': self.coworker.id, 'status': 'active',
            'name': 'Prompt B',
        })
        self.assertNotEqual(a.gateway_session_id, b.gateway_session_id)

    def test_header_carries_gateway_id(self):
        """(d) x-bf-session-id bär gateway-id:t, inte prompt-texten."""
        sess = self.Session.create({
            'coworker_id': self.coworker.id, 'status': 'active',
            'name': 'En prompt som är längre än sextiofyra tecken och '
                    'därför skulle ha klippts olika på två ställen',
        })
        headers = self.coworker._bifrost_session_headers(session=sess)
        self.assertTrue(headers, 'Headers ska byggas för en session')
        self.assertEqual(headers.get('x-bf-session-id'),
                         sess.gateway_session_id,
                         'Headern ska bära gateway-id:t exakt')

    def test_no_identity_sends_no_header(self):
        """(e) Session utan gateway-id skickar ingen falsk nyckel."""
        sess = self.Session.create({
            'coworker_id': self.coworker.id, 'status': 'active',
            'name': 'Gammal session',
        })
        # Simulera en session skapad före D1.
        sess.write({'gateway_session_id': False})
        headers = self.coworker._bifrost_session_headers(session=sess)
        # Headern får inte bära prompt-texten.
        if headers:
            self.assertNotEqual(headers.get('x-bf-session-id'), sess.name,
                                'Prompt-text får aldrig skickas som id')
