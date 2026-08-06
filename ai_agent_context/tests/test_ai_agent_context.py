# -*- coding: utf-8 -*-
##############################################################################
#
#    Copyright (C) 2024- Vertel AB (<https://vertel.se>).
#
##############################################################################

import json
import logging
from unittest.mock import patch, MagicMock

from odoo.tests.common import TransactionCase, HttpCase
from odoo.tests import tagged

_logger = logging.getLogger(__name__)


@tagged('ai_agent_context', 'post_install', '-at_install')
class TestContextCapture(TransactionCase):
    """Test the context capture and resolution logic."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.channel = cls.env['discuss.channel'].create({
            'name': 'Test AI Channel',
            'channel_member_ids': [
                (0, 0, {'partner_id': cls.env.user.partner_id.id}),
            ],
        })
        cls.partner = cls.env['res.partner'].create({
            'name': 'Test Customer',
            'email': 'test@example.com',
        })

    def test_resolve_context_sources_empty(self):
        """Context sources should all be None when nothing is set."""
        sources = self.channel._resolve_context_sources()
        self.assertIsNone(sources['env_model'])
        self.assertIsNone(sources['env_id'])
        self.assertIsNone(sources['http_model'])
        self.assertIsNone(sources['http_id'])
        self.assertIsNone(sources['channel_model'])
        self.assertIsNone(sources['channel_id'])

    def test_resolve_context_sources_from_env(self):
        """Context should be resolved from env.context (simulating quest.run)."""
        channel = self.channel.with_context(
            context_record_model='res.partner',
            context_record_id=self.partner.id,
            view_type='form',
        )
        sources = channel._resolve_context_sources()
        self.assertEqual(sources['env_model'], 'res.partner')
        self.assertEqual(sources['env_id'], self.partner.id)
        self.assertEqual(sources['env_view'], 'form')

    def test_resolve_context_sources_from_env_ai_prefix(self):
        """Context should also resolve _ai_context_model/_ai_context_id."""
        channel = self.channel.with_context(
            _ai_context_model='res.partner',
            _ai_context_id=self.partner.id,
        )
        sources = channel._resolve_context_sources()
        self.assertEqual(sources['env_model'], 'res.partner')
        self.assertEqual(sources['env_id'], self.partner.id)

    def test_resolve_context_sources_from_env_active(self):
        """Context should also resolve active_model/active_id."""
        channel = self.channel.with_context(
            active_model='res.partner',
            active_id=self.partner.id,
        )
        sources = channel._resolve_context_sources()
        self.assertEqual(sources['env_model'], 'res.partner')
        self.assertEqual(sources['env_id'], self.partner.id)

    def test_capture_ai_context_from_env(self):
        """_capture_ai_context should store context from env.context."""
        channel = self.channel.with_context(
            context_record_model='res.partner',
            context_record_id=self.partner.id,
        )
        channel._capture_ai_context()

        # Re-fetch to get written values
        channel.invalidate_recordset()
        self.assertEqual(channel.ai_context_model, 'res.partner')
        self.assertEqual(channel.ai_context_record_id, self.partner.id)

    def test_capture_ai_context_preserves_existing(self):
        """Existing context should not be overwritten by worse data."""
        # Set existing context
        self.channel.write({
            'ai_context_model': 'res.partner',
            'ai_context_record_id': self.partner.id,
        })

        # Try to capture with model-only data (no record_id)
        channel = self.channel.with_context(
            active_model='sale.order',
        )
        channel._capture_ai_context()

        channel.invalidate_recordset()
        # Should keep the original because it has record_id
        self.assertEqual(channel.ai_context_model, 'res.partner')
        self.assertEqual(channel.ai_context_record_id, self.partner.id)

    def test_set_channel_context_from_record(self):
        """set_channel_context should store context from a record object."""
        self.channel.set_channel_context(
            record=self.partner,
            view_type='form',
        )

        self.channel.invalidate_recordset()
        self.assertEqual(self.channel.ai_context_model, 'res.partner')
        self.assertEqual(self.channel.ai_context_record_id, self.partner.id)
        self.assertEqual(self.channel.ai_context_view_type, 'form')

    def test_set_channel_context_from_params(self):
        """set_channel_context should work with model/id params."""
        self.channel.set_channel_context(
            model='res.partner',
            record_id=self.partner.id,
            view_type='kanban',
        )

        self.channel.invalidate_recordset()
        self.assertEqual(self.channel.ai_context_model, 'res.partner')
        self.assertEqual(self.channel.ai_context_record_id, self.partner.id)
        self.assertEqual(self.channel.ai_context_view_type, 'kanban')

    def test_get_context_record(self):
        """get_context_record should resolve to the actual record."""
        self.channel.write({
            'ai_context_model': 'res.partner',
            'ai_context_record_id': self.partner.id,
        })

        record = self.channel.get_context_record()
        self.assertIsNotNone(record)
        self.assertEqual(record._name, 'res.partner')
        self.assertEqual(record.id, self.partner.id)

    def test_get_context_record_invalid(self):
        """get_context_record should return None for invalid context."""
        self.channel.write({
            'ai_context_model': 'no.such.model',
            'ai_context_record_id': 99999,
        })

        record = self.channel.get_context_record()
        self.assertIsNone(record)

    def test_get_context_record_no_context(self):
        """get_context_record should return None when context is empty."""
        record = self.channel.get_context_record()
        self.assertIsNone(record)


@tagged('ai_agent_context', 'post_install', '-at_install')
class TestRecordSerialization(TransactionCase):
    """Test record field serialization for AI context."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env['res.partner'].create({
            'name': 'Test Serialization Customer',
            'email': 'serialize@example.com',
            'phone': '+46123456789',
            'street': 'Testgatan 1',
            'city': 'Stockholm',
            'zip': '11122',
            'country_id': cls.env.ref('base.se').id,
        })

    def test_ai_serialize_fields_data(self):
        """Serialization should produce valid JSON with key fields."""
        json_str = self.partner._ai_serialize_fields_data()
        data = json.loads(json_str)

        self.assertIn('name', data)
        self.assertIn('email', data)
        self.assertEqual(data['name'], 'Test Serialization Customer')
        self.assertEqual(data['email'], 'serialize@example.com')

    def test_ai_serialize_truncates_long_values(self):
        """Long char fields should be truncated."""
        self.partner.write({'name': 'A' * 100})
        json_str = self.partner._ai_serialize_fields_data()
        data = json.loads(json_str)

        self.assertLess(len(data['name']), 60)
        self.assertIn('...', data['name'])

    def test_ai_serialize_excludes_binary(self):
        """Binary fields should not appear in serialization."""
        json_str = self.partner._ai_serialize_fields_data()
        data = json.loads(json_str)

        # image fields are binary — should not be in output
        for key in data.keys():
            self.assertNotIn('image', key.lower(),
                             f"Binary field {key} should not be serialized")

    def test_ai_build_record_context(self):
        """_ai_build_record_context should include record data."""
        ctx = self.partner._ai_build_record_context(caller_component='quest')
        self.assertTrue(len(ctx) > 0)
        # Should contain record data
        combined = '\n'.join(ctx)
        self.assertIn(self.partner._name, combined)
        self.assertIn('Test Serialization Customer', combined)


@tagged('ai_agent_context', 'post_install', '-at_install')
class TestChatterSerialization(TransactionCase):
    """Test chatter history serialization."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env['res.partner'].create({
            'name': 'Chatter Test Customer',
        })

    def test_ai_serialize_messages_data_empty(self):
        """Should return empty string for no messages."""
        result = self.partner._ai_serialize_messages_data()
        self.assertEqual(result, '')

    def test_ai_serialize_messages_data_with_messages(self):
        """Should return formatted chatter history."""
        self.partner.message_post(
            body='First message',
            message_type='comment',
            subtype_xmlid='mail.mt_note',
        )
        self.partner.message_post(
            body='Second message',
            message_type='comment',
            subtype_xmlid='mail.mt_comment',
        )

        result = self.partner._ai_serialize_messages_data()
        self.assertIn('First message', result)
        self.assertIn('Second message', result)
        # Messages should be in chronological order
        pos_first = result.find('First message')
        pos_second = result.find('Second message')
        self.assertLess(pos_first, pos_second,
                        "Messages should be oldest-first (chronological)")


@tagged('ai_agent_context', 'post_install', '-at_install')
class TestQuestContextInjection(TransactionCase):
    """Test quest-level context injection."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env['res.partner'].create({
            'name': 'Quest Context Customer',
            'email': 'quest@example.com',
        })

        # Create a test quest
        cls.quest = cls.env['ai.quest'].create({
            'name': 'Test Context Quest',
            'description': 'Test quest for context injection',
            'context_injection_enabled': True,
            'context_include_chatter': True,
        })

        cls.channel = cls.env['discuss.channel'].create({
            'name': 'Test Quest Channel',
            'channel_member_ids': [
                (0, 0, {'partner_id': cls.env.user.partner_id.id}),
            ],
        })

    def test_detect_record_from_env_context(self):
        """_detect_record should find record from env.context."""
        quest = self.quest.with_context(
            context_record_model='res.partner',
            context_record_id=self.partner.id,
        )
        record = quest._detect_record({})
        self.assertIsNotNone(record)
        self.assertEqual(record._name, 'res.partner')
        self.assertEqual(record.id, self.partner.id)

    def test_detect_record_from_kwargs_records(self):
        """_detect_record should find record from kwargs['records']."""
        record = self.quest._detect_record({
            'records': self.partner,
        })
        self.assertIsNotNone(record)
        self.assertEqual(record.id, self.partner.id)

    def test_detect_record_from_channel_context(self):
        """_detect_record should find record from channel's ai_context_*."""
        self.channel.write({
            'ai_context_model': 'res.partner',
            'ai_context_record_id': self.partner.id,
        })

        record = self.quest._detect_record({
            'channel': self.channel,
        })
        self.assertIsNotNone(record)
        self.assertEqual(record._name, 'res.partner')
        self.assertEqual(record.id, self.partner.id)

    def test_detect_record_returns_none_for_no_context(self):
        """_detect_record should return None when no context available."""
        record = self.quest._detect_record({})
        self.assertIsNone(record)

    def test_get_channel_context(self):
        """_get_channel_context should read from channel's ai_context_*."""
        self.channel.write({
            'ai_context_model': 'res.partner',
            'ai_context_record_id': self.partner.id,
            'ai_context_view_type': 'form',
        })

        quest = self.quest
        quest.channel_id = self.channel

        ctx = quest._get_channel_context()
        self.assertIsNotNone(ctx)
        self.assertEqual(ctx['model'], 'res.partner')
        self.assertEqual(ctx['record_id'], self.partner.id)
        self.assertEqual(ctx['view_type'], 'form')

    def test_extra_context_includes_record(self):
        """_extra_context should include record data when available."""
        self.channel.write({
            'ai_context_model': 'res.partner',
            'ai_context_record_id': self.partner.id,
        })

        quest = self.quest
        quest.channel_id = self.channel

        # Set env.context so _get_ai_context_record works
        quest_with_ctx = quest.with_context(
            _ai_context_model='res.partner',
            _ai_context_id=self.partner.id,
        )

        extra = quest_with_ctx._extra_context()
        self.assertIn('res.partner', extra)
        self.assertIn('Quest Context Customer', extra)


@tagged('ai_agent_context', 'post_install', '-at_install')
class TestController(HttpCase):
    """Test the JSONRPC endpoints."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env['res.partner'].create({
            'name': 'Controller Test Customer',
            'email': 'controller@example.com',
        })
        cls.channel = cls.env['discuss.channel'].create({
            'name': 'Test Controller Channel',
            'channel_member_ids': [
                (0, 0, {'partner_id': cls.env.user.partner_id.id}),
            ],
        })

    def test_set_context_rpc(self):
        """RPC set_context should store context on channel."""
        result = self.env['ir.http']._dispatch(
            '/ai_agent_context/set_context',
        )
        # Use make_jsonrpc_request for proper test
        self.authenticate('admin', 'admin')
        response = self.url_open(
            '/ai_agent_context/set_context',
            data=json.dumps({
                'jsonrpc': '2.0',
                'method': 'call',
                'params': {
                    'channel_id': self.channel.id,
                    'model': 'res.partner',
                    'res_id': self.partner.id,
                    'view_type': 'form',
                },
                'id': 1,
            }),
            headers={'Content-Type': 'application/json'},
        )
        self.assertEqual(response.status_code, 200)

        # Verify context was stored
        self.channel.invalidate_recordset()
        self.assertEqual(self.channel.ai_context_model, 'res.partner')
        self.assertEqual(self.channel.ai_context_record_id, self.partner.id)
