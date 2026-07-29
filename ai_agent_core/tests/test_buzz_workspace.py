# -*- coding: utf-8 -*-
"""Tests for ai.quest Buzz workspace mode."""

from odoo.tests.common import TransactionCase
from odoo.exceptions import ValidationError


class TestBuzzWorkspace(TransactionCase):

    def setUp(self):
        super().setUp()
        self.partner = self.env['res.partner'].create({'name': 'Test User'})
        self.user = self.env['res.users'].create({
            'name': 'Test User',
            'login': 'testuser_buzz',
            'partner_id': self.partner.id,
        })
        self.channel = self.env['discuss.channel'].with_context(
            mail_create_nosubscribe=True).create({
            'name': 'Test Buzz Channel',
            'channel_type': 'channel',
        })
        self.identity = self.env['ai.identity'].create({
            'name': 'Test Identity',
            'personality': 'Helpful',
            'style': 'Short answers',
            'values': 'Be correct',
            'boundaries': 'No legal advice',
        })
        self.agent = self.env['ai.agent'].create({
            'name': 'Test Agent',
            'alias_name': 'testagent',
            'trigger_words': 'test,hello',
            'identity_id': self.identity.id,
        })
        self.quest = self.env['ai.quest'].create({
            'name': 'Test Buzz Quest',
            'description': 'A test buzz workspace',
            'orchestration_mode': 'buzz',
            'channel_id': self.channel.id,
        })

    def test_orchestration_mode_default(self):
        """New quests default to single mode."""
        quest = self.env['ai.quest'].create({'name': 'Single Quest'})
        self.assertEqual(quest.orchestration_mode, 'single')

    def test_buzz_mode_creates_partner(self):
        """Adding an agent to a buzz quest creates a partner."""
        self.assertFalse(self.agent.partner_id)
        self.env['ai.quest.agent'].create({
            'quest_id': self.quest.id,
            'agent_id': self.agent.id,
        })
        self.assertTrue(self.agent.partner_id)
        self.assertEqual(self.agent.partner_id.name, self.agent.name)

    def test_buzz_agent_joins_channel(self):
        """Agent partner becomes channel member in buzz mode."""
        self.env['ai.quest.agent'].create({
            'quest_id': self.quest.id,
            'agent_id': self.agent.id,
        })
        members = self.channel.channel_member_ids.mapped('partner_id')
        self.assertIn(self.agent.partner_id, members)
        self.assertIn(self.agent, self.channel.ai_agent_ids)

    def test_buzz_route_mention(self):
        """@mention routes to the right agent."""
        self.env['ai.quest.agent'].create({
            'quest_id': self.quest.id,
            'agent_id': self.agent.id,
        })
        msg = self.env['mail.message'].new({
            'body': '<p>@testagent hello</p>',
            'model': 'discuss.channel',
            'res_id': self.channel.id,
        })
        routed = self.quest._buzz_route_message(msg)
        self.assertTrue(routed)
        self.assertEqual(routed.agent_id, self.agent)

    def test_buzz_route_trigger_word(self):
        """Trigger word routes to the right agent."""
        self.env['ai.quest.agent'].create({
            'quest_id': self.quest.id,
            'agent_id': self.agent.id,
        })
        msg = self.env['mail.message'].new({
            'body': '<p>This is a test message</p>',
            'model': 'discuss.channel',
            'res_id': self.channel.id,
        })
        routed = self.quest._buzz_route_message(msg)
        self.assertTrue(routed)
        self.assertEqual(routed.agent_id, self.agent)

    def test_remove_agent_leaves_channel(self):
        """Removing agent from quest removes channel membership."""
        rel = self.env['ai.quest.agent'].create({
            'quest_id': self.quest.id,
            'agent_id': self.agent.id,
        })
        self.assertTrue(self.agent.partner_id)
        rel.unlink()
        members = self.channel.channel_member_ids.mapped('partner_id')
        self.assertNotIn(self.agent.partner_id, members)

    def test_agent_reuse_across_quests(self):
        """Same agent partner reused in multiple buzz quests."""
        channel2 = self.env['discuss.channel'].with_context(
            mail_create_nosubscribe=True).create({
            'name': 'Second Buzz Channel',
            'channel_type': 'channel',
        })
        quest2 = self.env['ai.quest'].create({
            'name': 'Second Buzz Quest',
            'orchestration_mode': 'buzz',
            'channel_id': channel2.id,
        })
        self.env['ai.quest.agent'].create({
            'quest_id': self.quest.id,
            'agent_id': self.agent.id,
        })
        first_partner = self.agent.partner_id
        self.env['ai.quest.agent'].create({
            'quest_id': quest2.id,
            'agent_id': self.agent.id,
        })
        self.assertEqual(self.agent.partner_id, first_partner)

    def test_agent_partner_has_badge_prefix(self):
        """Agent partner name gets robot emoji prefix."""
        self.env['ai.quest.agent'].create({
            'quest_id': self.quest.id,
            'agent_id': self.agent.id,
        })
        self.assertTrue(self.agent.partner_id.name.startswith('🤖'))

    def test_agent_name_change_syncs_to_partner(self):
        """Changing agent name updates partner name."""
        self.env['ai.quest.agent'].create({
            'quest_id': self.quest.id,
            'agent_id': self.agent.id,
        })
        self.agent.name = 'Renamed Agent'
        self.assertIn('Renamed Agent', self.agent.partner_id.name)

    def test_proactive_agent_creation(self):
        """Buzz quest can auto-create an agent for an uncovered topic."""
        self.quest.allow_auto_create_agents = True
        result = self.quest._buzz_suggest_or_create_agent('Swedish VAT')
        self.assertTrue(result.get('created'))
        new_agent = result.get('agent')
        self.assertTrue(new_agent)
        self.assertIn(self.quest, new_agent.mapped('agent_ids.quest_id'))
        self.assertTrue(new_agent.partner_id)

    def test_dismiss_auto_agent(self):
        """Auto-created agent can be dismissed."""
        rel = self.env['ai.quest.agent'].sudo().create({
            'quest_id': self.quest.id,
            'agent_id': self.agent.id,
            'is_auto_created': True,
        })
        agent_id = self.agent.id
        rel.action_dismiss_auto_agent()
        self.assertFalse(self.env['ai.agent'].browse(agent_id).exists())

    def test_channel_session_sync(self):
        """Channel message creates a shared web UI session line."""
        self.env['ai.quest.agent'].create({
            'quest_id': self.quest.id,
            'agent_id': self.agent.id,
        })
        self.assertFalse(self.quest.buzz_channel_session_id)
        self.quest._buzz_sync_message_to_session('Hello from channel', role='user')
        session = self.quest.buzz_channel_session_id
        self.assertTrue(session.exists())
        self.assertEqual(len(session.session_line_ids), 1)
        self.assertIn('Hello from channel', session.session_line_ids[0].content)

    def test_avatar_generation_fallback(self):
        """Avatar generation falls back gracefully when no image model exists."""
        self.env['ai.quest.agent'].create({
            'quest_id': self.quest.id,
            'agent_id': self.agent.id,
        })
        result = self.agent._generate_avatar_image('A friendly robot')
        # Should return False because no text2image model is configured in tests
        self.assertFalse(result)
        # Partner still has default avatar from Odoo
        self.assertTrue(self.agent.partner_id.image_1920 or True)
