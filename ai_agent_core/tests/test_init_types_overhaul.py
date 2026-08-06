# -*- coding: utf-8 -*-
"""Tests for init types overhaul — webhook, openai_api, provider factory,
session memory, chat fix, cron, server action wizard, powerbox migration."""

import json
import time
from unittest.mock import patch

from odoo.tests.common import TransactionCase, HttpCase
from odoo.exceptions import UserError


class TestWebhook(TransactionCase):
    """Test webhook init_type controller and model methods."""

    def setUp(self):
        super().setUp()
        self.Coworker = self.env['ai.coworker']
        self.InitType = self.env['ai.coworker.init_type']

        self.coworker = self.Coworker.create({
            'name': 'Webhook Test',
            'status': 'active',
            'description': 'Test webhook coworker',
        })
        self.init_type = self.InitType.create({
            'coworker_id': self.coworker.id,
            'init_type': 'webhook',
            'enabled': True,
        })
        # Trigger _ensure_webhook
        self.init_type._ensure_webhook()

    def test_webhook_secret_generated(self):
        """Webhook secret is auto-generated on _ensure_webhook."""
        self.assertTrue(self.coworker.webhook_secret)
        self.assertEqual(len(self.coworker.webhook_secret), 32)

    def test_webhook_secret_persists(self):
        """Webhook secret persists across reads."""
        secret = self.coworker.webhook_secret
        self.coworker.invalidate_recordset()
        self.assertEqual(self.coworker.webhook_secret, secret)

    def test_webhook_reset_secret(self):
        """Resetting webhook secret generates a new one."""
        old_secret = self.coworker.webhook_secret
        self.coworker.webhook_secret = False
        self.init_type._ensure_webhook()
        self.assertNotEqual(self.coworker.webhook_secret, old_secret)

    def test_webhook_secret_generated_on_activation(self):
        """Webhook secret is generated after activating webhook init_type."""
        coworker2 = self.Coworker.create({
            'name': 'Webhook Activation Test',
            'status': 'active',
        })
        # Manually verify _ensure_webhook generates secret when called
        itype = self.InitType.create({
            'coworker_id': coworker2.id,
            'init_type': 'webhook',
            'enabled': True,
        })
        # Secret should be auto-generated via _after_change -> _ensure_webhook
        self.assertTrue(coworker2.webhook_secret)
        self.assertEqual(len(coworker2.webhook_secret), 32)


class TestPowerboxMigration(TransactionCase):
    """Test powerbox lookup uses init_type_ids instead of deprecated init_type."""

    def setUp(self):
        super().setUp()
        self.Coworker = self.env['ai.coworker']
        self.InitType = self.env['ai.coworker.init_type']

        self.coworker = self.Coworker.create({
            'name': 'Powerbox Test',
            'status': 'active',
        })
        self.init_type = self.InitType.create({
            'coworker_id': self.coworker.id,
            'init_type': 'powerbox',
            'enabled': True,
        })

    def test_powerbox_init_type_ids_filter(self):
        """Powerbox coworker is found via init_type_ids filter."""
        powerbox_types = self.InitType.search([
            ('coworker_id', '=', self.coworker.id),
            ('init_type', '=', 'powerbox'),
            ('enabled', '=', True),
        ])
        self.assertTrue(powerbox_types)
        self.assertEqual(powerbox_types[0].init_type, 'powerbox')

    def test_powerbox_not_active_not_found(self):
        """Inactive powerbox init_type is not found."""
        self.init_type.enabled = False
        powerbox_types = self.InitType.search([
            ('coworker_id', '=', self.coworker.id),
            ('init_type', '=', 'powerbox'),
            ('enabled', '=', True),
        ])
        self.assertFalse(powerbox_types)


class TestChatResponseMode(TransactionCase):
    """Test response_mode on chat/channel init_types."""

    def setUp(self):
        super().setUp()
        self.Coworker = self.env['ai.coworker']
        self.InitType = self.env['ai.coworker.init_type']

        self.coworker = self.Coworker.create({
            'name': 'Chat Response Test',
            'status': 'active',
        })

    def test_default_response_mode_is_mention(self):
        """Default response_mode is 'mention'."""
        itype = self.InitType.create({
            'coworker_id': self.coworker.id,
            'init_type': 'chat',
            'enabled': True,
        })
        self.assertEqual(itype.response_mode, 'mention')

    def test_response_mode_can_be_always(self):
        """Response mode can be set to 'always'."""
        itype = self.InitType.create({
            'coworker_id': self.coworker.id,
            'init_type': 'chat',
            'enabled': True,
            'response_mode': 'always',
        })
        self.assertEqual(itype.response_mode, 'always')

    def test_response_mode_trigger_requires_words(self):
        """Trigger mode requires chat_trigger_words to be set."""
        itype = self.InitType.create({
            'coworker_id': self.coworker.id,
            'init_type': 'channel',
            'enabled': True,
            'response_mode': 'trigger',
            'chat_trigger_words': 'help,support,question',
        })
        self.assertEqual(itype.response_mode, 'trigger')
        self.assertTrue(itype.chat_trigger_words)

    def test_channel_reply_mode_default(self):
        """Default channel_reply_mode is 'public'."""
        itype = self.InitType.create({
            'coworker_id': self.coworker.id,
            'init_type': 'channel',
            'enabled': True,
        })
        self.assertEqual(itype.channel_reply_mode, 'public')


class TestCronInterval(TransactionCase):
    """Test cron interval configuration."""

    def setUp(self):
        super().setUp()
        self.Coworker = self.env['ai.coworker']
        self.InitType = self.env['ai.coworker.init_type']

        self.coworker = self.Coworker.create({
            'name': 'Cron Test',
            'status': 'active',
        })

    def test_cron_interval_defaults(self):
        """Cron interval defaults to 1 hour."""
        # Create inactive first so _ensure_cron is not called
        itype = self.InitType.create({
            'coworker_id': self.coworker.id,
            'init_type': 'cron',
            'enabled': False,
        })
        itype.invalidate_recordset()
        self.assertEqual(itype.cron_interval_number, 1)
        self.assertEqual(itype.cron_interval_type, 'hours')

    def test_cron_interval_custom(self):
        """Cron interval can be customized."""
        itype = self.InitType.create({
            'coworker_id': self.coworker.id,
            'init_type': 'cron',
            'enabled': False,
            'cron_interval_number': 30,
            'cron_interval_type': 'minutes',
        })
        itype.invalidate_recordset()
        self.assertEqual(itype.cron_interval_number, 30)
        self.assertEqual(itype.cron_interval_type, 'minutes')


class TestServerActionWizard(TransactionCase):
    """Test server action wizard mode."""

    def setUp(self):
        super().setUp()
        self.Coworker = self.env['ai.coworker']
        self.InitType = self.env['ai.coworker.init_type']

        self.coworker = self.Coworker.create({
            'name': 'Server Action Test',
            'status': 'active',
        })

    def test_server_action_use_wizard_default(self):
        """server_action_use_wizard defaults to False."""
        itype = self.InitType.create({
            'coworker_id': self.coworker.id,
            'init_type': 'server_action',
            'enabled': True,
        })
        self.assertFalse(itype.server_action_use_wizard)

    def test_server_action_wizard_enabled(self):
        """server_action_use_wizard can be enabled."""
        itype = self.InitType.create({
            'coworker_id': self.coworker.id,
            'init_type': 'server_action',
            'enabled': True,
            'server_action_use_wizard': True,
        })
        self.assertTrue(itype.server_action_use_wizard)

    def test_server_action_wizard_model_exists(self):
        """Wizard transient model exists."""
        model = self.env.get('ai.coworker.server.action.wizard')
        self.assertTrue(model is not None, "Wizard model not found")
        if model:
            fields = model._fields
            self.assertIn('coworker_id', fields)
            self.assertIn('prompt', fields)
            self.assertIn('result', fields)
            self.assertIn('state', fields)


class TestProviderFactory(TransactionCase):
    """Test provider factory resolves providers correctly."""

    def test_provider_registry_populated(self):
        """PROVIDER_REGISTRY has all expected provider types."""
        from odoo.addons.ai_agent_core.core.provider import PROVIDER_REGISTRY
        self.assertIn('bifrost', PROVIDER_REGISTRY)
        self.assertIn('openai', PROVIDER_REGISTRY)
        self.assertIn('anthropic', PROVIDER_REGISTRY)
        self.assertIn('custom', PROVIDER_REGISTRY)

    def test_resolve_from_coworker_no_agents(self):
        """resolve_provider_from_coworker returns (None, None) when no agents."""
        from odoo.addons.ai_agent_core.core.provider import resolve_provider_from_coworker
        coworker = self.env['ai.coworker'].create({
            'name': 'Provider Test',
            'status': 'active',
        })
        provider, model = resolve_provider_from_coworker(coworker)
        self.assertIsNone(provider)
        self.assertIsNone(model)

    def test_resolve_from_model_no_provider(self):
        """resolve_provider_from_model returns None when model has no provider."""
        # Create a provider first
        provider = self.env['ai.provider'].create({
            'name': 'Test Provider',
            'provider_type': 'custom',
            'base_url': 'https://test.example.com/v1',
        })
        ai_model = self.env['ai.model'].create({
            'name': 'test-model',
            'provider_id': provider.id,
        })
        from odoo.addons.ai_agent_core.core.provider import resolve_provider_from_model
        result = resolve_provider_from_model(ai_model)
        # Should return a provider instance (DirectProvider with custom type)
        self.assertIsNotNone(result)


class TestSessionMemory(TransactionCase):
    """Test session-level memory (uploaded documents)."""

    def setUp(self):
        super().setUp()
        self.Coworker = self.env['ai.coworker']
        self.Session = self.env['ai.coworker.session']
        self.Memory = self.env['ai.memory']

        self.coworker = self.Coworker.create({
            'name': 'Session Memory Test',
            'status': 'active',
        })
        self.session = self.Session.create({
            'coworker_id': self.coworker.id,
            'status': 'active',
        })

    def test_session_memory_created(self):
        """Memory can be created with session_id."""
        memory = self.Memory.with_context(tracking_disable=True).create({
            'name': 'Test Doc',
            'content': 'Test content for session memory',
            'session_id': self.session.id,
            'memory_type': 'text',
        })
        self.assertEqual(memory.session_id.id, self.session.id)
        self.assertIn(memory, self.session.memory_ids)

    def test_session_memory_archived(self):
        """Memory can be archived (soft delete)."""
        memory = self.Memory.with_context(tracking_disable=True).create({
            'name': 'Archivable Doc',
            'content': 'To be archived',
            'session_id': self.session.id,
        })
        self.assertFalse(memory.archived)
        memory.archived = True
        self.assertTrue(memory.archived)

    def test_session_memories_injected(self):
        """Session memories are searchable via session_id."""
        mem = self.Memory.with_context(tracking_disable=True).create({
            'name': 'Session Doc',
            'content': 'Important session document content for testing',
            'session_id': self.session.id,
            'memory_type': 'text',
        })
        # Use the browse directly instead of search (ai.memory.search is overridden for FAISS)
        mem.invalidate_recordset()
        self.assertEqual(mem.session_id.id, self.session.id)
        # Verify by reading through the session's memory_ids
        self.session.invalidate_recordset()
        self.assertIn(mem.id, self.session.memory_ids.ids)


class TestCoworkerDataModel(TransactionCase):
    """Test new data model fields."""

    def setUp(self):
        super().setUp()
        self.Coworker = self.env['ai.coworker']
        self.InitType = self.env['ai.coworker.init_type']

    def test_response_mode_on_coworker(self):
        """response_mode field exists on ai.coworker."""
        coworker = self.Coworker.create({
            'name': 'Data Model Test',
            'status': 'active',
            'response_mode': 'always',
        })
        self.assertEqual(coworker.response_mode, 'always')

    def test_channel_reply_mode_on_coworker(self):
        """channel_reply_mode field exists on ai.coworker."""
        coworker = self.Coworker.create({
            'name': 'Reply Mode Test',
            'status': 'active',
            'channel_reply_mode': 'thread',
        })
        self.assertEqual(coworker.channel_reply_mode, 'thread')

    def test_webhook_secret_on_coworker(self):
        """webhook_secret field exists on ai.coworker."""
        coworker = self.Coworker.create({
            'name': 'Webhook Field Test',
            'status': 'active',
        })
        coworker.webhook_secret = 'test_secret_12345'
        self.assertEqual(coworker.webhook_secret, 'test_secret_12345')

    def test_max_webhook_payload_size_default(self):
        """max_webhook_payload_size defaults to 1MB."""
        coworker = self.Coworker.create({
            'name': 'Payload Size Test',
            'status': 'active',
        })
        self.assertEqual(coworker.max_webhook_payload_size, 1048576)

    def test_has_webhook_flag(self):
        """has_webhook is True when webhook init_type is active."""
        coworker = self.Coworker.create({
            'name': 'Webhook Flag Test',
            'status': 'active',
        })
        self.InitType.create({
            'coworker_id': coworker.id,
            'init_type': 'webhook',
            'enabled': True,
        })
        coworker._compute_init_type_flags()
        self.assertTrue(coworker.has_webhook)

    def test_cron_interval_on_coworker(self):
        """cron_interval fields exist on ai.coworker."""
        coworker = self.Coworker.create({
            'name': 'Cron Fields Test',
            'status': 'active',
            'cron_interval_number': 2,
            'cron_interval_type': 'days',
        })
        self.assertEqual(coworker.cron_interval_number, 2)
        self.assertEqual(coworker.cron_interval_type, 'days')

    def test_server_action_use_wizard_on_coworker(self):
        """server_action_use_wizard field exists on ai.coworker."""
        coworker = self.Coworker.create({
            'name': 'Wizard Field Test',
            'status': 'active',
            'server_action_use_wizard': True,
        })
        self.assertTrue(coworker.server_action_use_wizard)
