# -*- coding: utf-8 -*-
"""Tester för verktyget `push_notification` (push-notification 1.1).

Verktyget låter agenten avisera sessionens användare. Testerna prövar att
mottagaren alltid är sessionens `user_id`, att ett negativt svar är tydligt
(inga enheter / inga nycklar / ingen session), och att inget påstås om
leverans.
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
class TestPushNotificationTool(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        icp = cls.env['ir.config_parameter'].sudo()
        icp.set_param('mail.web_push_vapid_private_key', 'priv')
        icp.set_param('mail.web_push_vapid_public_key', 'pub')
        cls.user = create_user(cls.env, name='Push Tool User', login='push_tool_user_%s' % (cls.env.uid,))
        cls.coworker = cls.env['ai.coworker'].create({
            'name': 'Push Tool Coworker %s' % cls.env.uid,
        })

    def _session(self, user=None):
        return self.env['ai.coworker.session'].create({
            'coworker_id': self.coworker.id,
            'user_id': (user or self.user).id,
            'status': 'active',
        })

    def _env_for(self, session):
        """Env med sessionen i kontext — samma två nycklar som HITL läser."""
        return self.env(context={
            '_ai_context_model': 'ai.coworker.session',
            '_ai_context_id': session.id,
        })

    def _push_count(self):
        return self.env['mail.push'].sudo().search_count([])

    def _call(self, env=None, **kwargs):
        from odoo.addons.ai_agent_core.core.tools import (
            _tool_push_notification)
        return json.loads(_tool_push_notification(env or self.env, **kwargs))

    def test_tool_is_registered(self):
        """Verktyget finns i builtin_tools med rätt risknivå."""
        from odoo.addons.ai_agent_core.core.tools import builtin_tools
        tools = {t.name: t for t in builtin_tools()}
        self.assertIn('push_notification', tools)
        tool = tools['push_notification']
        self.assertEqual(tool.risk_level, 'write')
        self.assertIn('title', tool.parameters['properties'])
        self.assertIn('body', tool.parameters['properties'])
        self.assertIn('url', tool.parameters['properties'])
        # Mottagaren får ALDRIG vara en parameter (design D1).
        self.assertNotIn('user_id', tool.parameters['properties'])
        self.assertNotIn('recipient', tool.parameters['properties'])

    def test_queues_to_session_user(self):
        """En enhet finns → notisen köas till sessionens användare."""
        device = self.env['mail.push.device'].create({
            'partner_id': self.user.partner_id.id,
            'endpoint': 'https://push.example.com/tool-endpoint',
            'keys': _fake_keys(),
        })
        session = self._session()
        before = self._push_count()
        result = self._call(env=self._env_for(session),
                            title='Klart', body='Jobbet är klart.')
        self.assertTrue(result.get('queued'), result)
        self.assertEqual(self._push_count(), before + 1)
        push = self.env['mail.push'].sudo().search(
            [('mail_push_device_id', '=', device.id)], limit=1)
        self.assertTrue(push)
        payload = json.loads(push.payload)
        self.assertEqual(payload['title'], 'Klart')
        self.assertEqual(payload['body'], 'Jobbet är klart.')

    def test_no_devices_gives_clear_negative(self):
        """Utan enheter: tydligt negativt svar — inte påstående om leverans."""
        session = self._session()
        before = self._push_count()
        result = self._call(env=self._env_for(session),
                            title='Klart', body='x')
        self.assertFalse(result.get('queued'), result)
        self.assertIn('reason', result)
        self.assertEqual(self._push_count(), before,
                         'ingen push ska köas utan enheter')

    def test_missing_keys_gives_clear_negative(self):
        """Utan VAPID-nycklar: tydligt negativt svar."""
        self.env['mail.push.device'].create({
            'partner_id': self.user.partner_id.id,
            'endpoint': 'https://push.example.com/nokeys',
            'keys': _fake_keys(),
        })
        icp = self.env['ir.config_parameter'].sudo()
        priv = icp.get_param('mail.web_push_vapid_private_key')
        pub = icp.get_param('mail.web_push_vapid_public_key')
        icp.set_param('mail.web_push_vapid_private_key', '')
        icp.set_param('mail.web_push_vapid_public_key', '')
        self.addCleanup(icp.set_param,
                        'mail.web_push_vapid_private_key', priv)
        self.addCleanup(icp.set_param,
                        'mail.web_push_vapid_public_key', pub)

        session = self._session()
        result = self._call(env=self._env_for(session),
                            title='Klart', body='x')
        self.assertFalse(result.get('queued'), result)
        self.assertIn('VAPID', result.get('reason', ''))

    def test_no_session_in_context_is_a_clear_error(self):
        """Utan session går det inte att veta mottagaren — säg det."""
        result = self._call(title='Klart', body='x')
        self.assertFalse(result.get('queued'), result)
        self.assertIn('session', result.get('reason', '').lower())

    def test_empty_title_is_rejected(self):
        """Rubriken är obligatorisk — en tom notis är meningslös."""
        session = self._session()
        result = self._call(env=self._env_for(session), title='', body='x')
        self.assertFalse(result.get('queued'), result)
        self.assertIn('title', result.get('reason', ''))

    def test_does_not_notify_another_user(self):
        """Mottagaren är sessionens användare — inte den som anropar."""
        other = create_user(self.env, name='Push Other User', login='push_other_user_%s' % (self.env.uid,))
        self.env['mail.push.device'].create({
            'partner_id': other.partner_id.id,
            'endpoint': 'https://push.example.com/other-endpoint',
            'keys': _fake_keys(),
        })
        session = self._session()  # ägs av self.user (utan enheter)
        result = self._call(env=self._env_for(session),
                            title='Klart', body='x')
        self.assertFalse(
            result.get('queued'),
            'notisen ska gå till sessionens användare, som saknar enheter')
