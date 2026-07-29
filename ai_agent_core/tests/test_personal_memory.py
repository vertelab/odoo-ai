# -*- coding: utf-8 -*-
"""Tests för ai.personal.memory — personligt minne som följer användaren."""

import json
from datetime import datetime, timedelta

from odoo.tests import common, tagged
from odoo.exceptions import UserError


@tagged('-at_install', 'post_install')
class TestPersonalMemory(common.TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Skapa testanvändare
        cls.user = cls.env['res.users'].create({
            'name': 'Test User',
            'login': 'test_personal_memory@example.com',
            'email': 'test_personal_memory@example.com',
        })
        cls.company = cls.env.ref('base.main_company')

    # ════════════════════════════════════════════
    # T12.1: CRUD och ADD-only
    # ════════════════════════════════════════════

    def test_create_memory(self):
        """Skapa ett nytt personligt minne."""
        memory = self.env['ai.personal.memory'].add_memory(
            user_id=self.user.id,
            content='Test: Användaren föredrar korta svar utan emojis',
            category='preference',
            source='chat',
        )
        self.assertTrue(memory.id)
        self.assertEqual(memory.user_id.id, self.user.id)
        self.assertEqual(memory.category, 'preference')
        self.assertEqual(memory.source, 'chat')
        self.assertEqual(memory.importance, 'medium')
        self.assertFalse(memory.archived)
        self.assertTrue(memory.create_date)

    def test_add_only_prevents_update(self):
        """ADD-only: content får inte ändras efter skapande."""
        memory = self.env['ai.personal.memory'].add_memory(
            user_id=self.user.id,
            content='Original content',
            category='fact',
        )
        with self.assertRaises(UserError):
            memory.write({'content': 'Modified content'})

    def test_create_multiple_memories(self):
        """Skapa flera minnen för samma användare."""
        for i in range(5):
            self.env['ai.personal.memory'].add_memory(
                user_id=self.user.id,
                content=f'Memory {i}: test content',
                category='fact',
            )
        memories = self.env['ai.personal.memory'].search([
            ('user_id', '=', self.user.id),
            ('archived', '=', False),
        ])
        self.assertEqual(len(memories), 5)

    def test_archive_memory(self):
        """Arkivering av minne."""
        memory = self.env['ai.personal.memory'].add_memory(
            user_id=self.user.id,
            content='Test memory to archive',
            category='fact',
        )
        memory.write({'archived': True, 'archive_date': datetime.utcnow()})
        self.assertTrue(memory.archived)
        self.assertTrue(memory.archive_date)

        # Arkiverade minnen syns inte i vanlig sökning
        results = self.env['ai.personal.memory'].search_for_user(
            user_id=self.user.id, query='archive')
        self.assertNotIn(memory.id, [r.get('id') for r in results])

    def test_add_memory_no_user(self):
        """add_memory måste ha en giltig user_id."""
        with self.assertRaises(UserError):
            self.env['ai.personal.memory'].add_memory(
                user_id=999999,
                content='Should fail',
            )

    # ════════════════════════════════════════════
    # T12.2: Hybrid Search
    # ════════════════════════════════════════════

    def test_search_by_user(self):
        """Sökning efter användarens minnen."""
        self.env['ai.personal.memory'].add_memory(
            user_id=self.user.id,
            content='Jobbar med periodiseringsfond i K2',
            category='fact',
        )
        self.env['ai.personal.memory'].add_memory(
            user_id=self.user.id,
            content='Föredrar korta svar utan emojis',
            category='preference',
        )

        results = self.env['ai.personal.memory'].search_for_user(
            user_id=self.user.id)
        self.assertEqual(len(results), 2)

    def test_search_excludes_other_users(self):
        """En användares minnen syns inte för en annan användare."""
        user2 = self.env['res.users'].create({
            'name': 'User 2',
            'login': 'user2@test.com',
        })
        self.env['ai.personal.memory'].add_memory(
            user_id=self.user.id, content='User 1 memory', category='fact')
        self.env['ai.personal.memory'].add_memory(
            user_id=user2.id, content='User 2 memory', category='fact')

        results = self.env['ai.personal.memory'].search_for_user(
            user_id=self.user.id)
        for r in results:
            self.assertNotIn('User 2', r['content'])

    def test_search_empty_result(self):
        """Sökning utan matchning returnerar tom lista."""
        results = self.env['ai.personal.memory'].search_for_user(
            user_id=self.user.id, query='nothing_matchar_detta')
        self.assertEqual(len(results), 0)

    def test_search_threshold(self):
        """Threshold filtrerar bort låg-similaritetsträffar."""
        self.env['ai.personal.memory'].add_memory(
            user_id=self.user.id,
            content='Bokslut och periodiseringsfond',
            category='fact',
        )
        # Med threshold=0.9 (extremt högt) borde inget returneras
        results = self.env['ai.personal.memory'].search_for_user(
            user_id=self.user.id,
            query='något helt orelaterat',
            threshold=0.9,
        )
        self.assertEqual(len(results), 0)

    def test_search_explain(self):
        """Explain returnerar score_details."""
        self.env['ai.personal.memory'].add_memory(
            user_id=self.user.id,
            content='Test content for explain',
            category='fact',
        )
        results = self.env['ai.personal.memory'].search_for_user(
            user_id=self.user.id,
            query='explain',
            explain=True,
        )
        if results and 'score_details' in results[0]:
            self.assertIn('semantic', results[0]['score_details'])

    # ════════════════════════════════════════════
    # T12.3-12.4: Indexeringspipelines
    # ════════════════════════════════════════════

    def test_cron_daily_consolidation(self):
        """Konsolidering arkiverar gamla låg-importanta minnen."""
        # Skapa ett gammalt låg-important minne
        old_memory = self.env['ai.personal.memory'].create({
            'user_id': self.user.id,
            'content': 'Old low importance memory',
            'category': 'fact',
            'importance': 'low',
            'create_date': datetime.utcnow() - timedelta(days=60),
        })
        # Tvinga create_date (normalt auto)
        self.env.cr.execute(
            'UPDATE ai_personal_memory SET create_date = %s WHERE id = %s',
            (datetime.utcnow() - timedelta(days=60), old_memory.id))

        self.env['ai.personal.memory'].cron_daily_consolidation()

        old_memory.refresh()
        self.assertTrue(old_memory.archived)

    def test_nightly_cron_runs(self):
        """Nightly cron körs utan fel."""
        result = self.env['ai.personal.memory'].cron_nightly_index()
        self.assertIsInstance(result, dict)

    # ════════════════════════════════════════════
    # T12.6: System Prompt Injection
    # ════════════════════════════════════════════

    def test_build_system_prompt_block(self):
        """System prompt block genereras korrekt."""
        self.env['ai.personal.memory'].add_memory(
            user_id=self.user.id,
            content='Test: Användaren jobbar med K2',
            category='fact',
        )
        block = self.env['ai.personal.memory'].build_system_prompt_block(
            user_id=self.user.id, max_chars=2200)
        self.assertIn('USER PROFILE', block)
        self.assertIn('K2', block)
        self.assertIn('═' * 46, block)

    def test_build_system_prompt_block_empty(self):
        """Utan minnen returneras tom sträng."""
        block = self.env['ai.personal.memory'].build_system_prompt_block(
            user_id=self.user.id)
        self.assertEqual(block, '')

    # ════════════════════════════════════════════
    # T12.7: Embedding
    # ════════════════════════════════════════════

    def test_entity_extraction(self):
        """Entity extraction fungerar."""
        Memory = self.env['ai.personal.memory']
        entities = Memory._extract_entities(
            'Jobbar med K2 och periodiseringsfond i BAS-kontoplanen')
        self.assertTrue(any(e['text'] == 'K2' for e in entities))
        self.assertTrue(any('periodiseringsfond' in e['text']
                           for e in entities))

    def test_bm25_normalization(self):
        """BM25-normalisering returnerar värde i [0, 1]."""
        Memory = self.env['ai.personal.memory']
        score = Memory._normalize_bm25(7.0)
        self.assertAlmostEqual(score, 0.5, places=2)
        self.assertGreater(score, 0)
        self.assertLess(score, 1)

    # ════════════════════════════════════════════
    # T12.5: Mail via res.users Integration
    # ════════════════════════════════════════════

    def test_res_users_smart_button(self):
        """Smart button på res.users returnerar korrekt action."""
        action = self.user.action_open_personal_memory()
        self.assertEqual(action['res_model'], 'ai.personal.memory')
        self.assertEqual(action['type'], 'ir.actions.act_window')
        self.assertIn(('user_id', '=', self.user.id), action['domain'])

    def test_personal_memory_count(self):
        """personal_memory_count räknas korrekt."""
        self.user._compute_personal_memory_count()
        initial = self.user.personal_memory_count

        self.env['ai.personal.memory'].add_memory(
            user_id=self.user.id, content='Test count', category='fact')
        self.user._compute_personal_memory_count()
        self.assertEqual(self.user.personal_memory_count, initial + 1)
