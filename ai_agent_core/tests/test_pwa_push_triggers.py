# -*- coding: utf-8 -*-
"""Tester för AI-push-triggers (web-pwa-push): session klar + HITL väntar."""

import base64
import json

from odoo.tests.common import TransactionCase, tagged


def _fake_keys():
    return json.dumps({
        'p256dh': base64.urlsafe_b64encode(b'x' * 65).decode(),
        'auth': base64.urlsafe_b64encode(b'y' * 16).decode(),
    })


@tagged('-at_install', 'post_install')
class TestAiPushTriggers(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        icp = cls.env['ir.config_parameter'].sudo()
        icp.set_param('mail.web_push_vapid_private_key', 'priv')
        icp.set_param('mail.web_push_vapid_public_key', 'pub')
        cls.user = cls.env['res.users'].create({
            'name': 'Push Bob',
            'login': 'push_bob_%s' % cls.env['ir.sequence'].next_by_code('res.users') or '1',
            'partner_id': cls.env['res.partner'].create({'name': 'Push Bob'}).id,
        })
        cls.device = cls.env['mail.push.device'].create({
            'partner_id': cls.user.partner_id.id,
            'endpoint': 'https://push.example.com/endpoint',
            'keys': _fake_keys(),
        })

    def _push_count(self):
        return self.env['mail.push'].sudo().search_count([])

    def test_session_done_queues_push(self):
        session = self.env['ai.coworker.session'].create({
            'user_id': self.user.id,
        })
        before = self._push_count()
        session.mark_done()
        self.assertGreater(self._push_count(), before)

    def test_hitl_notify_queues_push(self):
        hitl = self.env['ai.coworker.hitl'].create({
            'user_id': self.user.id,
            'request_summary': 'Godkänn åtgärden',
            'action_type': 'create_record',
        })
        before = self._push_count()
        hitl._notify()
        self.assertGreater(self._push_count(), before)

    def test_silent_without_devices(self):
        other = self.env['res.users'].create({
            'name': 'NoDev',
            'login': 'nodev_%s' % (self.env['ir.sequence'].next_by_code('res.users') or '2'),
            'partner_id': self.env['res.partner'].create({'name': 'NoDev'}).id,
        })
        session = self.env['ai.coworker.session'].create({'user_id': other.id})
        before = self._push_count()
        session.mark_done()
        self.assertEqual(self._push_count(), before)

    def test_silent_without_vapid_keys(self):
        icp = self.env['ir.config_parameter'].sudo()
        icp.set_param('mail.web_push_vapid_private_key', '')
        icp.set_param('mail.web_push_vapid_public_key', '')
        session = self.env['ai.coworker.session'].create({'user_id': self.user.id})
        before = self._push_count()
        session.mark_done()  # ska inte kasta
        self.assertEqual(self._push_count(), before)
