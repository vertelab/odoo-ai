# -*- coding: utf-8 -*-
"""Tester för push vid tur-slut (push-notification 2.1–2.3).

Prövar aktivitetsgrinden (en användare som sitter kvar ska inte aviseras),
att `_finalize_stream_turn` aviserar en vanlig chattur, och att
stream- + webhook-vägen tillsammans ger exakt en notis.
"""

import base64
import json

from odoo.tests.common import TransactionCase, tagged
from ._test_users import create_user


def _fake_keys():
    return json.dumps({
        'p256dh': base64.urlsafe_b64encode(b'x' * 65).decode(),
        'auth': base64.urlsafe_b64encode(b'y' * 16).decode(),
    })


@tagged('post_install', '-at_install')
class TestTurnEndPush(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        icp = cls.env['ir.config_parameter'].sudo()
        icp.set_param('mail.web_push_vapid_private_key', 'priv')
        icp.set_param('mail.web_push_vapid_public_key', 'pub')
        cls.user = create_user(cls.env, name='Turn Push User', login='turn_push_user_%s' % (cls.env.uid,))
        cls.coworker = cls.env['ai.coworker'].create({
            'name': 'Turn Push Coworker %s' % cls.env.uid,
        })
        cls.env['mail.push.device'].create({
            'partner_id': cls.user.partner_id.id,
            'endpoint': 'https://push.example.com/turn-end',
            'keys': _fake_keys(),
        })

    def _session(self):
        return self.env['ai.coworker.session'].create({
            'coworker_id': self.coworker.id,
            'user_id': self.user.id,
            'status': 'active',
            'init_type': 'web_ui',
        })

    def _push_count(self):
        return self.env['mail.push'].sudo().search_count([])

    def _age_last_line(self, session, minutes):
        """Flytta sessionens senaste icke-assistent-rad bakåt i tiden.

        `create_date` är `automatic` — den sätts av ORM och kan inte
        skrivas med `write()`. Rå SQL är vägen (samma som
        `test_handover` använder för `write_date`).
        """
        line = self.env['ai.coworker.session.line'].sudo().search(
            [('session_id', '=', session.id), ('role', '!=', 'assistant')],
            order='create_date desc, id desc', limit=1)
        self.assertTrue(line, 'testet kräver en user-rad')
        self.env.cr.execute(
            "UPDATE ai_coworker_session_line "
            "   SET create_date = create_date - INTERVAL '%s minutes' "
            " WHERE id = %%s" % int(minutes), (line.id,))
        self.env.invalidate_all()

    def test_recent_activity_does_not_push(self):
        """2.1: användaren satt nyss → ingen notis."""
        session = self._session()
        self.env['ai.coworker.session.line'].create({
            'session_id': session.id, 'role': 'user',
            'content': 'fråga', 'sequence': 1,
        })
        self.env['ai.coworker.session.line'].create({
            'session_id': session.id, 'role': 'assistant',
            'content': 'svar', 'sequence': 2,
        })
        before = self._push_count()
        session._push_done_notification()
        self.assertEqual(self._push_count(), before,
                         'en användare som sitter kvar ska inte aviseras')

    def test_idle_user_is_pushed(self):
        """2.1: användaren lämnade för länge sedan → notis köas."""
        session = self._session()
        self.env['ai.coworker.session.line'].create({
            'session_id': session.id, 'role': 'user',
            'content': 'fråga', 'sequence': 1,
        })
        self.env['ai.coworker.session.line'].create({
            'session_id': session.id, 'role': 'assistant',
            'content': 'svar', 'sequence': 2,
        })
        self._age_last_line(session, minutes=5)
        before = self._push_count()
        session._push_done_notification()
        self.assertEqual(self._push_count(), before + 1,
                         'en övergiven session ska aviseras')

    def test_threshold_is_configurable(self):
        """2.1: gränsen läses ur systemparametern."""
        icp = self.env['ir.config_parameter'].sudo()
        old = icp.get_param('ai_agent_core.push_idle_seconds')
        icp.set_param('ai_agent_core.push_idle_seconds', '0')
        self.addCleanup(
            icp.set_param, 'ai_agent_core.push_idle_seconds', old or '')
        session = self._session()
        self.env['ai.coworker.session.line'].create({
            'session_id': session.id, 'role': 'user',
            'content': 'fråga', 'sequence': 1,
        })
        self.env['ai.coworker.session.line'].create({
            'session_id': session.id, 'role': 'assistant',
            'content': 'svar', 'sequence': 2,
        })
        before = self._push_count()
        session._push_done_notification()
        self.assertEqual(self._push_count(), before + 1,
                         'gräns 0 ska alltid avisera')

    def test_finalize_calls_push_notification(self):
        """2.2: `_finalize_stream_turn` anropar aviseringen.

        Finaliseringen öppnar en EGEN cursor (den måste överleva att
        klienten kopplar ner), och Odoo förbjuder `commit()` inifrån ett
        test — testets data är därför osynlig för den cursorn. Vi prövar
        kontraktet på källnivå: funktionen ska anropa
        `_push_done_notification` inuti sitt kast-säkra block.
        """
        import inspect
        from odoo.addons.ai_agent_core.controllers import stream as stream_mod
        source = inspect.getsource(stream_mod._finalize_stream_turn)
        self.assertIn('_push_done_notification', source,
                      'finaliseringen ska avisera turen')
        # Kast-säkert: anropet ligger i ett try-block med except efter.
        idx = source.index('_push_done_notification')
        block = source[max(0, idx - 600):idx + 600]
        self.assertIn('try:', block,
                      'aviseringen ska ligga i ett kast-säkert block')
        self.assertIn('except Exception', block,
                      'ett fel i pushen får inte kasta vidare')

    def test_push_failure_does_not_block_persist(self):
        """2.2: ett fel i pushen hindrar inte att raden skrivs."""
        from unittest.mock import patch
        from odoo.addons.ai_agent_core.controllers import stream as stream_mod
        from odoo.addons.ai_agent_core.models import ai_session as sess_mod

        session = self._session()

        def _boom(self):
            raise RuntimeError('push nere')

        with patch.object(sess_mod.AICoworkerSession,
                          '_push_done_notification', _boom):
            # Får inte kasta vidare.
            ok = stream_mod._finalize_stream_turn(
                self.env.cr.dbname, self.env.uid, self.env.context,
                session.id, ['svaret'], {'finish_reason': 'stop'})
        self.assertTrue(ok, 'finaliseringen ska lyckas trots push-fel')

    def test_claim_prevents_double_notification(self):
        """2.3: spärren gör att samma assistentrad bara aviseras en gång."""
        session = self._session()
        self.env['ai.coworker.session.line'].create({
            'session_id': session.id, 'role': 'user',
            'content': 'fråga', 'sequence': 1,
        })
        self.env['ai.coworker.session.line'].create({
            'session_id': session.id, 'role': 'assistant',
            'content': 'svar', 'sequence': 2,
        })
        self.assertTrue(session._claim_push_notification(),
                        'första anropet ska ta spärren')
        session.invalidate_recordset(['push_notified_line_id'])
        self.assertFalse(session._claim_push_notification(),
                         'andra anropet för samma rad ska nekas')

    def test_double_push_path_gives_one_notification(self):
        """2.3: stream + mark_done för samma tur ger EXAKT en notis."""
        session = self._session()
        self.env['ai.coworker.session.line'].create({
            'session_id': session.id, 'role': 'user',
            'content': 'fråga', 'sequence': 1,
        })
        self.env['ai.coworker.session.line'].create({
            'session_id': session.id, 'role': 'assistant',
            'content': 'svar', 'sequence': 2,
        })
        self._age_last_line(session, minutes=5)
        before = self._push_count()
        # Båda vägarna anropar samma funktion; spärren ska ge en notis.
        session._push_done_notification()
        session._push_done_notification()
        self.assertEqual(self._push_count(), before + 1,
                         'samma tur får inte ge två notiser')

    def test_new_turn_after_notification_pushes_again(self):
        """2.3: en NY tur (ny assistentrad) får avisera igen."""
        session = self._session()
        self.env['ai.coworker.session.line'].create({
            'session_id': session.id, 'role': 'user',
            'content': 'fråga 1', 'sequence': 1,
        })
        self.env['ai.coworker.session.line'].create({
            'session_id': session.id, 'role': 'assistant',
            'content': 'svar 1', 'sequence': 2,
        })
        self._age_last_line(session, minutes=5)
        before = self._push_count()
        session._push_done_notification()
        self.assertEqual(self._push_count(), before + 1)

        self.env['ai.coworker.session.line'].create({
            'session_id': session.id, 'role': 'user',
            'content': 'fråga 2', 'sequence': 3,
        })
        self.env['ai.coworker.session.line'].create({
            'session_id': session.id, 'role': 'assistant',
            'content': 'svar 2', 'sequence': 4,
        })
        # Bara user-raden åldras: assistentraden är den NYA turens och ska
        # ligga efter föregående notis — det är precis det spärren jämför.
        self._age_last_line(session, minutes=5)
        session._push_done_notification()
        self.assertEqual(self._push_count(), before + 2,
                         'en ny tur ska kunna avisera igen')
