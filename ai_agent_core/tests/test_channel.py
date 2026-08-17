# -*- coding: utf-8 -*-
"""Tester för channel-adapter-kontraktet (core/channel.py).

Körs under Odoo-test-runner (TransactionCase) — logiken är ren Python.
"""

import asyncio

from odoo.tests.common import TransactionCase

from odoo.addons.ai_agent_core.core.channel import (
    ChannelRegistry,
    NormalizedItem,
    process_item,
    satisfies_adapter,
    satisfies_processor,
)


class MockProcessor:
    """Processor som mockar klassificering → disposition."""

    def __init__(self, classification=None, approval=True):
        self.classification = classification or {
            'disposition': 'nudge',
        }
        self.approval = approval
        self.calls = {'dispose': [], 'hitl': [], 'nudge': [], 'remember': 0}

    async def classify(self, item):
        return self.classification

    def dispose(self, item, disposition):
        self.calls['dispose'].append(disposition)

    def hitl(self, item, action_type, context):
        self.calls['hitl'].append((action_type, context))
        return type('Approval', (), {'approved': self.approval})()

    def nudge(self, item, message):
        self.calls['nudge'].append(message)

    def remember(self, item):
        self.calls['remember'] += 1


class MockAdapter:
    """Adapter som uppfyller ChannelAdapter-kontraktet (duck-typing)."""

    def fetch_new(self, user, since=None):
        return []

    def normalize(self, raw):
        return NormalizedItem(channel='mock', external_id=str(raw))

    def dispose(self, item, disposition):
        pass

    def draft_outbound(self, user, item, content):
        return {'folder': 'Drafts', 'uid': 1}

    def send_outbound(self, user, item, content):
        pass


class TestChannelContract(TransactionCase):

    def test_normalized_item(self):
        item = NormalizedItem(
            channel='mail', external_id='<x@y>', sender='a@b.se',
            content='Hej', received_at='2026-08-09T10:00:00',
            attachments=[{'filename': 'a.ics', 'content_type': 'text/calendar'}])
        self.assertEqual(item.channel, 'mail')
        self.assertEqual(item.attachments[0]['filename'], 'a.ics')

    def test_registry_register_and_lookup(self):
        reg = ChannelRegistry()
        reg.register('mock', adapter=MockAdapter(), processor=MockProcessor())
        self.assertEqual(reg.channels(), ['mock'])
        self.assertIsNotNone(reg.get_adapter('mock'))
        self.assertIsNotNone(reg.get_processor('mock'))
        self.assertIsNone(reg.get_adapter('missing'))

    def test_satisfies_adapter(self):
        self.assertTrue(satisfies_adapter(MockAdapter()))
        self.assertFalse(satisfies_adapter(object()))
        self.assertFalse(satisfies_adapter(None))

    def test_satisfies_processor(self):
        self.assertTrue(satisfies_processor(MockProcessor()))
        self.assertFalse(satisfies_processor(object()))

    def test_process_item_dispatch(self):
        reg = ChannelRegistry()
        proc = MockProcessor(classification={'disposition': 'create',
                                             'hitl_required': True,
                                             'action_type': 'promote',
                                             'hitl_context': {'res_id': 1}})
        reg.register('mock', adapter=MockAdapter(), processor=proc)
        item = NormalizedItem(channel='mock', external_id='1', content='Hej')
        result = asyncio.run(process_item(item, registry=reg))
        self.assertEqual(result, 'create')
        self.assertEqual(proc.calls['dispose'], ['create'])
        self.assertEqual(proc.calls['hitl'][0][0], 'promote')
        self.assertEqual(proc.calls['remember'], 1)

    def test_process_item_waiting_hitl(self):
        reg = ChannelRegistry()
        proc = MockProcessor(classification={'disposition': 'link',
                                             'hitl_required': True},
                             approval=False)
        reg.register('mock', adapter=MockAdapter(), processor=proc)
        item = NormalizedItem(channel='mock', external_id='2')
        result = asyncio.run(process_item(item, registry=reg))
        self.assertEqual(result, 'waiting_hitl')
        self.assertEqual(proc.calls['dispose'], [],
                         "Ingen dispose före godkännande")

    def test_process_item_no_processor(self):
        item = NormalizedItem(channel='okänd', external_id='3')
        result = asyncio.run(process_item(item))
        self.assertEqual(result, 'no_processor')

    def test_process_item_nudge_message(self):
        reg = ChannelRegistry()
        proc = MockProcessor(classification={'disposition': 'nudge',
                                             'nudge_message': 'Viktigt!'})
        reg.register('mock', adapter=MockAdapter(), processor=proc)
        item = NormalizedItem(channel='mock', external_id='4')
        result = asyncio.run(process_item(item, registry=reg))
        self.assertEqual(result, 'nudge')
        self.assertEqual(proc.calls['nudge'], ['Viktigt!'])

    def test_reference_user_mail_ai_satisfies(self):
        """Referensverifiering: user_mail_ai uppfyller kontraktet strukturellt.

        Körs endast när user_mail_ai är installerad (annars skippas tyst).
        """
        if 'user_mail_ai.mail' not in self.env:
            return
        Mail = self.env['user_mail_ai.mail']
        # duck-typing: modellen har de metoder kontraktet kräver (via
        # user.mail.imap-hook + pipeline) — utan migrering
        self.assertTrue(hasattr(Mail, '_ingest_message'))
        self.assertTrue(hasattr(Mail, '_process_new_for_user'))
