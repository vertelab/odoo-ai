from odoo.tests import TransactionCase
import logging

_logger = logging.getLogger(__name__)

class TestAiQuest(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        #Test Chat
        cls.quest_chat_record = cls.env.ref('ai_agent.ai_quest_code_chat')
        cls.quest_chat_record_user_id = cls.quest_chat_record.chat_user_id
        cls.quest_chat_record_partner_id = cls.quest_chat_record_user_id.partner_id
        
        #Test Channel
        cls.quest_channel_record = cls.env.ref('ai_agent.ai_quest_code_channel')
        cls.quest_channel_record_channel_id = cls.quest_channel_record.channel_id
        
        cls.demo_user = cls.env.ref('base.user_demo')  # Use env.ref() not ref()
        cls.demo_partner = cls.demo_user.partner_id

    def test_message_sending_chat(self):
        """Test message delivery to quest record's user"""
        # 1. Verify recipient exists
        self.assertTrue(
            self.quest_chat_record.user_id.partner_id,
            "Quest record has no user assigned"
        )
        partners = (self.demo_partner + self.quest_chat_record_partner_id).ids
        channel = self.env['discuss.channel'].with_user(self.demo_user).channel_get(partners)
        # 2. Send message in channel
        message = channel.with_user(self.demo_user).message_post(body="How do i write in a 1 on 1 channel in odoo 18 using python.")
        
        
        
    def test_message_sending_channel(self):
        """Test message delivery to quest record's user"""
        # 1. Verify recipient exists
        self.assertTrue(
            self.quest_channel_record_channel_id,
            "Quest record has no channel assigned"
        )
        # 2. Send message in channel
        message = self.quest_channel_record_channel_id.with_user(self.demo_user).message_post(body="How do i write in a channel in odoo 18 using python.")


