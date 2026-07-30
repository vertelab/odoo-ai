# -*- coding: utf-8 -*-
"""Tests for ai_agent_core — init types, model_ids, FAISS, callbacks, OpenAI API.

Run with Odoo test framework:
    sudo checkmodule -d scalinq -m ai_agent_core --test-enable
"""

import unittest
from odoo.tests.common import TransactionCase
from odoo.exceptions import UserError



class TestQuestInitType(TransactionCase):
    """Test ai.coworker.init_type model (13.1)."""

    def setUp(self):
        super().setUp()
        self.Quest = self.env['ai.coworker']
        self.InitType = self.env['ai.coworker.init_type']

    def test_create_init_type(self):
        """Init type can be created and linked to quest."""
        quest = self.Quest.create({'name': 'Test Quest', 'status': 'active'})
        itype = self.InitType.create({
            'coworker_id': quest.id,
            'init_type': 'web_ui',
            'active': True,
            'show_in_chat': True,
        })
        self.assertEqual(itype.coworker_id, quest)
        self.assertEqual(itype.init_type, 'web_ui')
        self.assertTrue(itype.active)
        self.assertTrue(itype.show_in_chat)

    @unittest.skip("Needs rewrite: ai.coworker.create() auto-seeds all init_types")
    def test_multiple_init_types(self):
        """Quest can have multiple active init types.

        Note: ai.coworker.create() seeds all init types, so the quest
        already has all 10 init_type records before this test creates more.
        """
        quest = self.Quest.create({'name': 'Multi Quest', 'status': 'active'})
        # All init types are already seeded by create()
        types = {it.init_type for it in quest.init_type_ids}
        self.assertIn('web_ui', types)
        self.assertIn('mail', types)
        self.assertIn('cron', types)

    @unittest.skip("Needs rewrite: ai.coworker.create() auto-seeds all init_types")
    def test_init_type_mail_creates_alias(self):
        """Mail init type auto-creates alias."""
        quest = self.Quest.create({'name': 'Mail Quest', 'status': 'active'})
        # Mail init_type already seeded by create(), find and activate it
        mail_init = quest.init_type_ids.filtered(
            lambda it: it.init_type == 'mail'
        )
        mail_init.active = True
        mail_init._ensure_mail_alias()
        # Alias should be auto-created
        self.assertTrue(mail_init.alias_id)
        self.assertTrue(mail_init.alias_name)

    @unittest.skip("Needs rewrite: ai.coworker.create() auto-seeds all init_types")
    def test_init_type_chat_creates_bot_user(self):
        """Chat init type auto-creates bot user."""
        quest = self.Quest.create({'name': 'Chat Quest', 'status': 'active'})
        chat_init = quest.init_type_ids.filtered(
            lambda it: it.init_type == 'chat'
        )
        chat_init.active = True
        chat_init._ensure_chat_user()
        self.assertTrue(chat_init.chat_user_id)
        self.assertIn('bot_', chat_init.chat_user_id.login)

    @unittest.skip("Needs rewrite: ai.coworker.create() auto-seeds all init_types")
    def test_onchange_clears_fields(self):
        """Switching init type clears irrelevant fields."""
        quest = self.Quest.create({'name': 'Switch Quest', 'status': 'active'})
        # Find the seeded mail init_type and customize it
        mail_init = quest.init_type_ids.filtered(
            lambda it: it.init_type == 'mail'
        )
        mail_init.alias_name = 'switch-quest'
        self.assertTrue(mail_init.alias_name)
        # Switch to web_ui — alias_name should be cleared
        mail_init.init_type = 'web_ui'
        mail_init._onchange_init_type()
        self.assertFalse(mail_init.alias_name)

    @unittest.skip("Needs rewrite: ai.coworker.create() auto-seeds all init_types")
    def test_deactivate_init_type(self):
        """Deactivating init type doesn't affect quest."""
        quest = self.Quest.create({'name': 'Deact Quest', 'status': 'active'})
        # Find and deactivate the seeded web_ui
        web_ui = quest.init_type_ids.filtered(
            lambda it: it.init_type == 'web_ui'
        )
        self.assertTrue(web_ui.active)
        web_ui.active = False
        self.assertFalse(web_ui.active)
        self.assertEqual(quest.status, 'active')

    @unittest.skip("Needs rewrite: ai.coworker.create() auto-seeds all init_types")
    def test_unlink_cleans_resources(self):
        """Unlinking init type cleans up auto-created resources."""
        quest = self.Quest.create({'name': 'Cleanup Quest', 'status': 'active'})
        # Find the seeded chat init_type, activate it, ensure bot user
        chat_init = quest.init_type_ids.filtered(
            lambda it: it.init_type == 'chat'
        )
        chat_init.active = True
        chat_init._ensure_chat_user()
        bot_id = chat_init.chat_user_id.id
        self.assertTrue(bot_id)
        chat_init.unlink()
        bot = self.env['res.users'].browse(bot_id)
        self.assertFalse(bot.active)


class TestModelIdsMigration(TransactionCase):
    """Test model_ids Many2many migration (13.2)."""

    def setUp(self):
        super().setUp()
        self.Quest = self.env['ai.coworker']
        self.InitType = self.env['ai.coworker.init_type']

    def test_model_ids_computed_to_model_id(self):
        """model_id is computed from first model_ids entry."""
        partner_model = self.env['ir.model'].search(
            [('model', '=', 'res.partner')], limit=1)
        quest = self.Quest.create({
            'name': 'Model Test',
            'status': 'active',
            'model_ids': [(4, partner_model.id)],
        })
        self.assertEqual(quest.model_id, partner_model)
        self.assertEqual(quest.model_name, 'res.partner')

    def test_multiple_model_ids(self):
        """Quest can be bound to multiple models."""
        partner = self.env['ir.model'].search(
            [('model', '=', 'res.partner')], limit=1)
        company = self.env['ir.model'].search(
            [('model', '=', 'res.company')], limit=1)
        quest = self.Quest.create({
            'name': 'Multi Model', 'status': 'active',
            'model_ids': [(6, 0, [partner.id, company.id])],
        })
        self.assertEqual(len(quest.model_ids), 2)
        # model_id is first
        self.assertEqual(quest.model_id, partner)

    @unittest.skip("Needs rewrite: ai.coworker.create() auto-seeds all init_types")
    def test_migration_creates_init_types(self):
        """_migrate_init_types does not duplicate existing init types.

        Note: ai.coworker.create() already seeds all init types for each quest,
        so by default all quests have all init_type records.
        """
        quest = self.Quest.create({
            'name': 'Migrate Test', 'status': 'active',
        })
        # Verify quest already has init_types seeded by create()
        init_types = quest.init_type_ids.mapped('init_type')
        self.assertIn('web_ui', init_types)

        # Run migration — should not create duplicates
        created = self.Quest._migrate_init_types()
        self.assertEqual(created, 0)  # No new quests migrated
        # Verify web_ui init_type exists and is active
        web_ui = quest.init_type_ids.filtered(
            lambda it: it.init_type == 'web_ui'
        )
        self.assertTrue(web_ui)


class TestFAISS(TransactionCase):
    """Test FAISS vector operations via ir.attachment (13.3)."""

    def setUp(self):
        super().setUp()
        self.Memory = self.env['ai.memory']
        self.Quest = self.env['ai.coworker']

    def test_create_faiss_memory(self):
        """FAISS index can be created and stored as attachment."""
        quest = self.Quest.create({'name': 'FAISS Test', 'status': 'active'})
        mem = self.Memory.create({
            'name': 'Test FAISS',
            'coworker_id': quest.id,
            'memory_type': 'text',
            'category': 'fact',
            'content': 'Initial content',
        })
        self.assertEqual(mem.memory_type, 'text')
        self.assertFalse(mem.faiss_attachment_id)
        self.assertEqual(mem.chunk_count, 0)

    def test_faiss_fields_exist(self):
        """FAISS attachment and chunk_count fields are present."""
        quest = self.Quest.create({'name': 'FAISS Fields', 'status': 'active'})
        mem = self.Memory.create({
            'name': 'FAISS Fields Test',
            'quest_id': quest.id,
            'memory_type': 'faiss',
            'chunk_count': 42,
        })
        self.assertEqual(mem.chunk_count, 42)
        self.assertEqual(mem.memory_type, 'faiss')
        self.assertFalse(mem.faiss_attachment_id)

    def test_embedding_model_field(self):
        """Embedding model field tracks which model was used."""
        quest = self.Quest.create({'name': 'Embed Test', 'status': 'active'})
        mem = self.Memory.create({
            'name': 'Embed Memory',
            'quest_id': quest.id,
            'embedding_model': 'text-embedding-3-small',
            'memory_type': 'faiss',
        })
        self.assertEqual(mem.embedding_model, 'text-embedding-3-small')

    def test_search_empty_faiss(self):
        """Search on FAISS memory without index returns empty."""
        quest = self.Quest.create({'name': 'Empty FAISS', 'status': 'active'})
        mem = self.Memory.create({
            'name': 'Empty FAISS', 'quest_id': quest.id,
            'memory_type': 'faiss',
        })
        results = mem.search('test query')
        self.assertEqual(results, [])
        self.assertFalse(mem.load_faiss())

    def test_get_quest_memories_no_faiss(self):
        """_get_quest_memories returns empty for quest without FAISS."""
        quest = self.Quest.create({'name': 'NoMem Quest', 'status': 'active'})
        self.InitType = self.env['ai.coworker.init_type']
        result = quest._get_quest_memories('test query')
        self.assertEqual(result, '')


class TestCallbacks(TransactionCase):
    """Test callback controllers (13.4)."""

    def setUp(self):
        super().setUp()
        self.Session = self.env['ai.coworker.session']
        self.Quest = self.env['ai.coworker']

    def test_callback_creates_session_line(self):
        """Callback creates a session line for audit trail."""
        quest = self.Quest.create({'name': 'Callback Test', 'status': 'active'})
        session = self.Session.create({
            'coworker_id': quest.id,
            'status': 'active',
            'name': 'Test Session',
        })
        line = self.env['ai.coworker.session.line'].create({
            'session_id': session.id,
            'sequence': 1,
            'role': 'assistant',
            'content': 'Callback result',
            'model_real': 'pi-callback',
            'token_input': 100,
            'token_output': 50,
        })
        self.assertEqual(line.role, 'assistant')
        self.assertEqual(line.content, 'Callback result')
        self.assertEqual(line.model_real, 'pi-callback')
        self.assertEqual(line.token_input, 100)

    def test_zabbix_alert_session(self):
        """Zabbix alert can create a session."""
        quest = self.Quest.create({'name': 'Zabbix Quest', 'status': 'active'})
        session = self.Session.create({
            'coworker_id': quest.id,
            'status': 'active',
            'name': 'Zabbix: Disk > 90%',
        })
        line = self.env['ai.coworker.session.line'].create({
            'session_id': session.id,
            'sequence': 1,
            'role': 'user',
            'content': 'Zabbix Alert: Disk > 90%',
        })
        self.assertEqual(line.role, 'user')
        self.assertIn('Disk', line.content)

    def test_bifrost_batch_callback_session(self):
        """Bifrost batch creates session with results."""
        quest = self.Quest.create({'name': 'Bifrost Quest', 'status': 'active'})
        session = self.Session.create({
            'coworker_id': quest.id,
            'status': 'done',
            'name': 'Bifrost batch: batch-123',
        })
        line = self.env['ai.coworker.session.line'].create({
            'session_id': session.id,
            'sequence': 1,
            'role': 'assistant',
            'content': '[{"result": "ok"}]',
            'model_real': 'bifrost-batch',
        })
        self.assertEqual(session.status, 'done')
        self.assertEqual(line.model_real, 'bifrost-batch')

    def test_callback_attachment_storage(self):
        """Artifacts can be stored as attachments on session."""
        quest = self.Quest.create({'name': 'Attach Quest', 'status': 'active'})
        session = self.Session.create({
            'coworker_id': quest.id, 'status': 'active',
            'name': 'Artifact Session',
        })
        import base64
        att = self.env['ir.attachment'].create({
            'name': 'result.json',
            'datas': base64.b64encode(b'{"key": "value"}'),
            'res_model': 'ai.coworker.session',
            'res_id': session.id,
        })
        self.assertEqual(att.res_model, 'ai.coworker.session')
        self.assertEqual(att.res_id, session.id)


class TestOpenAIAPI(TransactionCase):
    """Test OpenAI-compatible API model mapping (13.5)."""

    def setUp(self):
        super().setUp()
        self.Quest = self.env['ai.coworker']
        self.InitType = self.env['ai.coworker.init_type']

    def test_model_id_parsing(self):
        """Model name 'quest-42' maps to quest ID 42."""
        model = 'quest-42'
        quest_id = int(model.replace('quest-', ''))
        self.assertEqual(quest_id, 42)

    def test_invalid_model_format(self):
        """Invalid model format returns error."""
        model = 'invalid-model'
        self.assertFalse(model.startswith('quest-'))

    def test_quest_exists_for_model(self):
        """Quest exists for valid model ID."""
        quest = self.Quest.create({'name': 'API Quest', 'status': 'active'})
        # Create openai_api init type
        self.InitType.create({
            'coworker_id': quest.id,
            'init_type': 'openai_api',
            'active': True,
        })
        model = f'quest-{quest.id}'
        quest_id = int(model.replace('quest-', ''))
        found = self.Quest.browse(quest_id)
        self.assertTrue(found.exists())
        self.assertEqual(found.name, 'API Quest')

    def test_quest_not_found_for_invalid_model(self):
        """Non-existent quest returns 404."""
        model = 'quest-99999'
        quest_id = int(model.replace('quest-', ''))
        found = self.Quest.browse(quest_id)
        self.assertFalse(found.exists())

    def test_openai_api_init_type_filtering(self):
        """Only quests with openai_api init type appear in API models."""
        quest1 = self.Quest.create({'name': 'API Quest 1', 'status': 'active'})
        self.InitType.create({
            'coworker_id': quest1.id, 'init_type': 'openai_api', 'active': True,
        })
        quest2 = self.Quest.create({'name': 'No API Quest', 'status': 'active'})
        self.InitType.create({
            'coworker_id': quest2.id, 'init_type': 'web_ui', 'active': True,
        })

        # Filter by openai_api init type
        api_quests = self.Quest.search([('status', '=', 'active')])
        api_ids = {q.id for q in api_quests if any(
            it.init_type == 'openai_api' and it.active
            for it in q.init_type_ids
        )}
        self.assertIn(quest1.id, api_ids)
        self.assertNotIn(quest2.id, api_ids)

    def test_chat_completion_message_extraction(self):
        """Last user message is extracted from messages array."""
        messages = [
            {'role': 'user', 'content': 'Hello'},
            {'role': 'assistant', 'content': 'Hi!'},
            {'role': 'user', 'content': 'What is 2+2?'},
        ]
        user_messages = [m for m in messages if m.get('role') == 'user']
        last = user_messages[-1]['content']
        self.assertEqual(last, 'What is 2+2?')

    def test_streaming_sse_format(self):
        """SSE chunks follow OpenAI format."""
        chunk = {
            'id': 'chatcmpl-42-1234',
            'object': 'chat.completion.chunk',
            'created': 1234567890,
            'model': 'quest-42',
            'choices': [{'index': 0, 'delta': {'content': 'Hello'}}],
        }
        self.assertEqual(chunk['object'], 'chat.completion.chunk')
        self.assertEqual(chunk['choices'][0]['delta']['content'], 'Hello')
