# -*- coding: utf-8 -*-
"""Tester för avisering när ett schemalagt jobb är klart (push-notification 3).

En `ai.org.task` som körs av heartbeat ska avisera beställaren när den
checkas in — med uppgiftens namn och resultatet. En uppgift utan beställare
ska inte avisera någon, och en misslyckad push får aldrig påverka
incheckningen.
"""

import base64
import json

from odoo.tests.common import TransactionCase, tagged


def _fake_keys():
    return json.dumps({
        'p256dh': base64.urlsafe_b64encode(b'x' * 65).decode(),
        'auth': base64.urlsafe_b64encode(b'y' * 16).decode(),
    })


@tagged('post_install', '-at_install')
class TestTaskRequesterNotification(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        icp = cls.env['ir.config_parameter'].sudo()
        icp.set_param('mail.web_push_vapid_private_key', 'priv')
        icp.set_param('mail.web_push_vapid_public_key', 'pub')
        cls.requester = cls.env['res.users'].create({
            'name': 'Requester User',
            'login': 'requester_user_%s' % (cls.env.uid,),
        })
        cls.coworker = cls.env['ai.coworker'].create({
            'name': 'Requester Coworker %s' % cls.env.uid,
        })

    def _device(self, user):
        return self.env['mail.push.device'].create({
            'partner_id': user.partner_id.id,
            'endpoint': 'https://push.example.com/req-%s' % user.id,
            'keys': _fake_keys(),
        })

    def _task(self, requester=None, **kw):
        vals = {
            'name': 'Nattlig rapport',
            'description': 'Sammanställ gårdagens ordrar',
            'coworker_id': self.coworker.id,
        }
        if requester is not None:
            vals['beställare_ref'] = '%s,%s' % (
                requester._name, requester.id)
        vals.update(kw)
        return self.env['ai.org.task'].create(vals)

    def _push_count(self):
        return self.env['mail.push'].sudo().search_count([])

    def test_requester_is_notified(self):
        """3.1/3.2: en uppgift med beställare aviserar beställaren."""
        self._device(self.requester)
        task = self._task(requester=self.requester)
        task.write({'result_summary': 'Rapporten ligger i dokumentmappen.'})
        before = self._push_count()
        queued = self.coworker._notify_task_requester(task)
        self.assertTrue(queued, 'beställaren ska aviseras')
        self.assertEqual(self._push_count(), before + 1)
        push = self.env['mail.push'].sudo().search([], order='id desc', limit=1)
        payload = json.loads(push.payload)
        self.assertIn('Nattlig rapport', payload['title'])
        self.assertIn('dokumentmappen', payload['body'])

    def test_task_without_requester_notifies_nobody(self):
        """3.1: en uppgift utan beställare aviserar ingen."""
        # Coworkerns egen användare har en enhet — den får inte användas
        # när uppgiften saknar beställare och ingen session finns.
        self._device(self.requester)
        task = self._task(requester=None)
        before = self._push_count()
        queued = self.coworker._notify_task_requester(task)
        self.assertFalse(queued, 'ingen beställare → ingen notis')
        self.assertEqual(self._push_count(), before)

    def test_falls_back_to_session_user(self):
        """3.1/D5: beställare som är en agent → sessionens användare."""
        self._device(self.requester)
        agent = self.env['ai.agent'].create({
            'name': 'Requester Agent %s' % self.env.uid,
        })
        task = self._task(requester=None)
        task.write({'beställare_ref': 'ai.agent,%s' % agent.id})
        session = self.env['ai.coworker.session'].create({
            'coworker_id': self.coworker.id,
            'user_id': self.requester.id,
            'status': 'active',
        })
        before = self._push_count()
        queued = self.coworker._notify_task_requester(task, session)
        self.assertTrue(queued, 'sessionens användare ska aviseras')
        self.assertEqual(self._push_count(), before + 1)

    def test_push_failure_does_not_affect_checkin(self):
        """3.3: en misslyckad push påverkar inte incheckningen."""
        task = self._task(requester=self.requester)  # ingen enhet
        task.action_checkin('Klart ändå.')
        self.assertEqual(task.status, 'done')
        self.assertEqual(task.result_summary, 'Klart ändå.')
        # Aviseringen returnerar False men kastar inte.
        queued = self.coworker._notify_task_requester(task)
        self.assertFalse(queued)
        self.assertEqual(task.status, 'done',
                         'incheckningen ska stå kvar')

    def test_notify_never_raises(self):
        """3.1: kast-säker — ett fel får inte bubbla upp."""
        task = self._task(requester=self.requester)
        from unittest.mock import patch
        with patch.object(
                type(self.env['web.pwa.push']),
                '_push_user_notification',
                side_effect=RuntimeError('push nere')):
            # Får inte kasta.
            result = self.coworker._notify_task_requester(task)
        self.assertFalse(result)
