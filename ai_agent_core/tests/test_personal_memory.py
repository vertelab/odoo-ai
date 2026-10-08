# -*- coding: utf-8 -*-
"""Tests för ai.personal.memory — personligt minne som följer användaren."""

import json
from datetime import datetime

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

    # FYND 2026-10-07 (calendar-events-okf-scoping, task 2.3):
    # `test_cron_daily_consolidation` och `test_nightly_cron_runs` togs bort
    # tillsammans med metoderna de testade. `cron_daily_consolidation`,
    # `cron_nightly_index`, `cron_index_calendar` och `cron_index_chats`
    # hade INGEN `ir.cron` — de kördes aldrig, och testerna bevisade bara att
    # en död metod kunde anropas manuellt.
    #
    # Kalenderindexeringen flyttar till `calendar_ai`-bryggan. Historisk
    # chattindexering är en egen fråga; live-vägen
    # (`ai.personal.memory.cron_extract_from_discuss`, discuss-learning-cron)
    # testas separat.

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
        """Smartknappen på res.users öppnar det LEVANDE minnet.

        Kontraktet ändrades (odoo-mind-memory-scope-isolation): knappen
        visar `ai.okf.concept` (personal-scope, ägt av användaren) — inte
        legacy `ai.personal.memory`. Legacy-vägen injiceras inte.
        """
        action = self.user.action_open_personal_memory()
        self.assertEqual(action['res_model'], 'ai.okf.concept')
        self.assertEqual(action['type'], 'ir.actions.act_window')
        self.assertIn(('scope', '=', 'personal'), action['domain'])
        self.assertIn(('owner_user_id', '=', self.user.id), action['domain'])

    # ════════════════════════════════════════════
    # Smartknappar på de tre ägarna (odoo-mind-memory-scope-isolation)
    # ════════════════════════════════════════════

    def _mk_concept(self, key, scope, owner_user=None, owner_company=None,
                    owner_coworker=None, source_user=None):
        atype = self.env['ai.artifact.type'].search([], limit=1)
        self.env.cr.execute("""
            INSERT INTO ai_okf_concept
                (concept_key, summary, scope, version, status, archived,
                 artifact_type_id, owner_user_id, owner_company_id,
                 owner_coworker_id, source_user_id, create_date, write_date)
            VALUES (%s, %s, %s, 1, 'stable', false, %s, %s, %s, %s, %s,
                    now(), now())
            RETURNING id
        """, (key, key, scope, atype.id,
              owner_user.id if owner_user else None,
              owner_company.id if owner_company else None,
              owner_coworker.id if owner_coworker else None,
              source_user.id if source_user else None))
        return self.env.cr.fetchone()[0]

    def test_company_smart_button(self):
        """res.company-knappen öppnar company-scope ägt av bolaget."""
        action = self.company.action_open_company_memory()
        self.assertEqual(action['res_model'], 'ai.okf.concept')
        self.assertIn(('scope', '=', 'company'), action['domain'])
        self.assertIn(('owner_company_id', '=', self.company.id),
                      action['domain'])

    def test_coworker_smart_button(self):
        """ai.coworker-knappen öppnar coworker-scope ägt av medarbetaren."""
        coworker = self.env['ai.coworker'].create({
            'name': 'GUI Coworker', 'status': 'active'})
        action = coworker.action_get_memory()
        self.assertEqual(action['res_model'], 'ai.okf.concept')
        self.assertIn(('scope', '=', 'coworker'), action['domain'])
        self.assertIn(('owner_coworker_id', '=', coworker.id),
                      action['domain'])

    def test_is_my_memory_filter(self):
        """'Mina minnen' = personliga + coworker lärda ur min session."""
        other = self.env['res.users'].create({
            'name': 'Other', 'login': 'other_mymem@example.com'})
        coworker = self.env['ai.coworker'].create({
            'name': 'MyMem Coworker', 'status': 'active'})
        # Min personliga
        self._mk_concept('mymem.personal', 'personal',
                         owner_user=self.user)
        # Annans personliga
        self._mk_concept('mymem.other', 'personal', owner_user=other)
        # Coworker lärd ur MIN session
        self._mk_concept('mymem.cw.mine', 'coworker',
                         owner_coworker=coworker, source_user=self.user)
        # Coworker lärd ur annans session
        self._mk_concept('mymem.cw.other', 'coworker',
                         owner_coworker=coworker, source_user=other)
        # Coworker-global (kaizen)
        self._mk_concept('mymem.cw.global', 'coworker',
                         owner_coworker=coworker)

        Concept = self.env['ai.okf.concept'].with_user(self.user)
        mine = Concept.search([('is_my_memory', '=', True)])
        keys = set(mine.mapped('concept_key'))
        self.assertIn('mymem.personal', keys)
        self.assertIn('mymem.cw.mine', keys)
        self.assertNotIn('mymem.other', keys)
        self.assertNotIn('mymem.cw.other', keys)
        self.assertNotIn('mymem.cw.global', keys,
                         'coworker-globalt minne är inte "mitt"')

    def test_okf_counts_are_owner_scoped(self):
        """Räknarna visar bara den egna ägarens koncept."""
        other = self.env['res.users'].create({
            'name': 'Other2', 'login': 'other_counts@example.com'})
        self._mk_concept('cnt.mine', 'personal', owner_user=self.user)
        self._mk_concept('cnt.other', 'personal', owner_user=other)
        self.user._compute_okf_memory_count()
        self.assertEqual(self.user.okf_memory_count, 1)

        coworker = self.env['ai.coworker'].create({
            'name': 'Cnt Coworker', 'status': 'active'})
        self._mk_concept('cnt.cw', 'coworker', owner_coworker=coworker)
        coworker._compute_okf_memory_count()
        self.assertEqual(coworker.okf_memory_count, 1)

    def test_personal_memory_count(self):
        """personal_memory_count räknas korrekt."""
        self.user._compute_personal_memory_count()
        initial = self.user.personal_memory_count

        self.env['ai.personal.memory'].add_memory(
            user_id=self.user.id, content='Test count', category='fact')
        self.user._compute_personal_memory_count()
        self.assertEqual(self.user.personal_memory_count, initial + 1)


@tagged('-at_install', 'post_install')
class TestDiscussLearning(common.TransactionCase):
    """discuss-learning-cron: idempotens i cron_extract_from_discuss.

    Vägen läser gårdagens publika kanalmeddelanden och skriver till
    avsändarens personliga minne. Den ska vara idempotent per meddelande
    (source_ref='discuss,<id>'), så att en dubbelkörning inte ger
    dubbletter.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Memory = cls.env['ai.personal.memory']
        cls.user = cls.env['res.users'].create({
            'name': 'Discuss Learner',
            'login': 'discuss_learner@example.com',
            'email': 'discuss_learner@example.com',
            'learn_from_discuss': True,
        })

    def _channel_with_messages(self, count=3):
        channel = self.env['discuss.channel'].create({
            'name': 'Testkanal %s' % self.user.id,
            'channel_type': 'channel',
        })
        # Gårdagens datum (cronen läser igår).
        from datetime import date, timedelta, datetime as dt
        y = date.today() - timedelta(days=1)
        when = dt(y.year, y.month, y.day, 12, 0, 0)
        msgs = self.env['mail.message']
        for i in range(count):
            m = self.env['mail.message'].create({
                'model': 'discuss.channel',
                'res_id': channel.id,
                'message_type': 'comment',
                'body': '<p>Meddelande %d från användaren</p>' % i,
                'author_id': self.user.partner_id.id,
            })
            # create_date är readonly — sätt via SQL för att hamna på igår.
            self.env.cr.execute(
                "UPDATE mail_message SET create_date = %s WHERE id = %s",
                (when, m.id))
            m.invalidate_recordset(['create_date'])
            msgs |= m
        return channel, msgs

    def test_second_run_skips_processed_messages(self):
        """Ett meddelande som redan bidragit hoppas över (idempotens)."""
        channel, msgs = self._channel_with_messages(3)
        ref = 'discuss,%d' % msgs[0].id
        # Första körningen: skapa ett minne med source_ref.
        self.Memory.add_memory(
            user_id=self.user.id, content='Redan extraherad',
            category='fact', source='discuss_chat', source_ref=ref)
        # Grinden: meddelandet finns redan för användaren.
        already = self.Memory.search([
            ('user_id', '=', self.user.id),
            ('source_ref', '=', ref),
        ], limit=1)
        self.assertTrue(already, 'grinden ska hitta det redan extraherade')

    def test_gate_is_user_scoped(self):
        """Grinden är per användare — samma meddelande, olika användare."""
        other = self.env['res.users'].create({
            'name': 'Other Learner',
            'login': 'other_learner@example.com',
            'email': 'other_learner@example.com',
        })
        ref = 'discuss,999999'
        self.Memory.add_memory(
            user_id=self.user.id, content='Användare A',
            category='fact', source='discuss_chat', source_ref=ref)
        # Samma source_ref för en annan användare ska inte finnas.
        other_hit = self.Memory.search([
            ('user_id', '=', other.id),
            ('source_ref', '=', ref),
        ], limit=1)
        self.assertFalse(other_hit, 'grinden ska vara user_id-scoped')
        # Men den egna finns.
        own_hit = self.Memory.search([
            ('user_id', '=', self.user.id),
            ('source_ref', '=', ref),
        ], limit=1)
        self.assertTrue(own_hit)

    def test_cron_skips_already_processed_message(self):
        """Ett meddelande som redan gett ett minne ingår inte i batchen.

        Kör den riktiga cron-metoden med en mockad provider. Grinden ska
        göra att ett redan extraherat meddelande inte bidrar igen.
        """
        from unittest.mock import patch
        channel, msgs = self._channel_with_messages(4)
        ref = 'discuss,%d' % msgs[0].id
        self.Memory.add_memory(
            user_id=self.user.id, content='Redan extraherad',
            category='fact', source='discuss_chat', source_ref=ref)

        captured = {}

        class _FakeResult:
            text = '{"memories": []}'

        class _FakeLoop:
            def __init__(self, *a, **k):
                pass

            def run(self, prompt):
                captured['prompt'] = prompt

                async def _coro():
                    return _FakeResult()
                return _coro()

        with patch(
                'odoo.addons.ai_agent_core.core.provider.get_default_provider',
                return_value=(object(), None)), \
             patch(
                'odoo.addons.ai_agent_core.core.loop.AgentLoop',
                _FakeLoop):
            self.Memory.cron_extract_from_discuss()

        # Prompten ska bara innehålla de icke-redan-extraherade meddelandena.
        prompt = captured.get('prompt', '')
        self.assertNotIn('Meddelande 0', prompt,
                         'det redan extraherade meddelandet ska hoppas över')
        self.assertIn('Meddelande 1', prompt)
        self.assertIn('Meddelande 2', prompt)
        self.assertIn('Meddelande 3', prompt)
