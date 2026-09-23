# -*- coding: utf-8 -*-
"""Tester för embedding-produktion och efterfyllnad (okf-recall-path fas 3).

Täcker fas 1 (metoderna finns och beter sig) och fas 3 (kolumnen blir
levande, efterfyllnad fungerar, markeringen är sanningsenlig).

VARFÖR DESSA TESTER FINNS: hela ändringen handlar om att något SÅG byggt ut
men var tomt i drift. Ett test som bara kontrollerar att koden kör utan
krasch hade inte fångat det — därför kontrollerar dessa tester den faktiska
markeringen (`embedding_state`) och att en efterfyllnad faktiskt ändrar
databasen.
"""

from unittest.mock import patch

from odoo.tests import common, tagged

import logging
_logger = logging.getLogger(__name__)


@tagged('okf', 'post_install', '-at_install')
class TestEmbeddingProduction(common.TransactionCase):
    """Fas 1 + 3: vektorn ska UPPSTÅ, inte kräva att någon kommer ihåg den."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.ref('base.main_company')
        cls.Concept = cls.env['ai.okf.concept']
        cls.Provider = cls.env['ai.provider']
        cls.atype = cls.env['ai.artifact.type'].search(
            [('name', '=', 'knowledge')], limit=1)
        if not cls.atype:
            cls.atype = cls.env['ai.artifact.type'].create({
                'name': 'knowledge', 'kind': 'knowledge',
            })

    def _fake_vector(self, dim=1024, value=0.1):
        return [value] * dim

    def _patch_embedding(self, vector=None):
        """Mocka BÅDE provider-valet och själva anropet.

        `_okf_cron_backfill_embeddings` gör två saker i tur och ordning:
          1. `_embedding_provider()` — väljer provider (returnerar None i en
             test-DB utan `can_embed`-provider, och då avbryter cronen med
             "ingen provider som kan embedda" INNAN `_get_embedding` nås).
          2. `_get_embedding()` — det faktiska HTTP-anropet.

        Att bara mocka (2) räcker inte: cronen faller på (1). Det var
        därför test_backfill_* FAILade i ren nyinstallation (FYND 2026-09-23).

        Returnerar en context manager som mockar båda.
        """
        from contextlib import ExitStack
        vec = vector if vector is not None else self._fake_vector()
        provider = self.env['ai.provider'].create({
            'name': 'Test-embedding-provider',
            'provider_type': 'bifrost',
            'can_embed': True,
            'api_key': 'test-key',
        })
        stack = ExitStack()
        stack.enter_context(patch.object(
            type(self.env['ai.provider']), '_embedding_provider',
            return_value=provider))
        stack.enter_context(patch.object(
            type(self.env['ai.provider']), '_get_embedding',
            return_value=vec))
        return stack

    # ── fas 1.3/1.3b: metoderna finns och validerar ──

    def test_get_embedding_returns_raw_list(self):
        """`_get_embedding` ska ge en RÅ lista, inte ett langchain-objekt.

        Detta är kärnan i D2: den gamla koden returnerade olika saker i
        olika moduler, vilket gjorde API:et omöjligt att lita på.
        """
        provider = self.Provider.search([('provider_type', '=', 'openai')],
                                        limit=1)
        if not provider:
            self.skipTest('ingen openai-provider i testdatabasen')
        vec = [0.1] * 1024
        payload = {"data": [{"index": 0, "embedding": vec}]}
        with patch.object(type(provider), '_embedding_post',
                          return_value=payload):
            result = provider._get_embedding(model='text-embedding-3-small',
                                             input='test')
        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 1024)
        self.assertEqual(result, vec)

    def test_get_embedding_batch_sorts_by_index(self):
        """Batch-svaret ska sorteras på `index` — ordningen är ett kontrakt.

        Utan sortering kan vektor N hamna på koncept M, vilket är tyst
        datakorruption som är omöjlig att upptäcka i efterhand.
        """
        provider = self.Provider.search([('provider_type', '=', 'openai')],
                                        limit=1)
        if not provider:
            self.skipTest('ingen openai-provider i testdatabasen')
        # Avsiktligt i OORDNING — api:et lovar inte ordningen
        vec_a, vec_b = [0.1] * 1024, [0.2] * 1024
        payload = {"data": [
            {"index": 1, "embedding": vec_b},
            {"index": 0, "embedding": vec_a}]}
        with patch.object(type(provider), '_embedding_post',
                          return_value=payload):
            result = provider._get_embedding_batch(
                model='text-embedding-3-small', inputs=['a', 'b'])
        self.assertEqual(result[0], vec_a)
        self.assertEqual(result[1], vec_b)

    def test_get_embedding_batch_wrong_count_returns_all_none(self):
        """Fel antal vektorer → alla None. Hellre tomt än felkopplat."""
        provider = self.Provider.search([('provider_type', '=', 'openai')],
                                        limit=1)
        if not provider:
            self.skipTest('ingen openai-provider i testdatabasen')
        payload = {"data": [{"index": 0, "embedding": [0.1] * 1024}]}
        with patch.object(type(provider), '_embedding_post',
                          return_value=payload):
            result = provider._get_embedding_batch(
                model='text-embedding-3-small', inputs=['a', 'b', 'c'])
        self.assertEqual(result, [None, None, None])

    def test_validate_embedding_rejects_wrong_dimension(self):
        """Fel dimension ska underkännas — annars blir kolumnen korrupt tyst."""
        provider = self.Provider.search([], limit=1)
        if not provider:
            self.skipTest('ingen provider i testdatabasen')
        self.assertTrue(
            provider._validate_embedding(self._fake_vector(1024),
                                         model='text-embedding-3-small',
                                         dim=1024))
        self.assertFalse(
            provider._validate_embedding(self._fake_vector(1536),
                                         model='text-embedding-3-small',
                                         dim=1024))

    # ── fas 3.1/3.2: upsert producerar vektor och märker raden ──

    def test_okf_upsert_without_embedding_arg_sets_state(self):
        """`_okf_upsert` utan `embedding`-arg ska ALLTID sätta en markering.

        Testet kräver inte att en vektor skapas (testmiljön har ingen
        gateway) — det kräver att raden är ärlig om vad som hände. Det är
        skillnaden mellan att veta och att gissa.
        """
        with patch.object(type(self.env['ai.okf.concept']),
                          '_produce_embedding',
                          return_value=(self._fake_vector(), 'ready')):
            concept = self.Concept._okf_upsert(
                artifact_type=self.atype,
                concept_key='test.embedding.ready',
                summary='Testkoncept för vektormarkering',
                title='Testkoncept',
                owner_company_id=self.company.id,
            )
        self.assertEqual(concept.embedding_state, 'ready')
        self.assertIsNotNone(concept.embedding)

    def test_okf_upsert_marks_pending_when_provider_fails(self):
        """Provider utan svar → 'pending' (inte en tyst tom rad).

        Poängen är ärligheten: en rad utan vektor ska SÄGA att den saknar
        vektor. Tidigare blev `embedding` tyst None utan att någon kunde
        skilja "aldrig försökt" från "försökte och misslyckades".
        """
        with patch.object(type(self.env['ai.provider']), '_get_embedding',
                          return_value=None):
            concept = self.Concept._okf_upsert(
                artifact_type=self.atype,
                concept_key='test.embedding.pending',
                summary='Koncept utan tillgänglig provider',
                title='Pending-koncept',
                owner_company_id=self.company.id,
            )
        self.assertEqual(concept.embedding_state, 'pending')
        self.assertFalse(concept.embedding)

    def test_explicit_wrong_dimension_marks_failed(self):
        """Explicit vektor med fel dimension → 'failed' + ingen vektor sparas."""
        provider = self.Provider.search([], limit=1)
        if not provider:
            self.skipTest('ingen provider i testdatabasen')
        concept = self.Concept._okf_upsert(
            artifact_type=self.atype,
            concept_key='test.embedding.failed',
            summary='Koncept med felaktig vektor',
            title='Failed-koncept',
            owner_company_id=self.company.id,
            embedding=self._fake_vector(1536),
        )
        self.assertEqual(concept.embedding_state, 'failed')
        self.assertFalse(concept.embedding)

    def test_empty_summary_marks_skipped(self):
        """Ingen text att vektorisera → 'skipped', inte 'failed'."""
        concept = self.Concept._okf_upsert(
            artifact_type=self.atype,
            concept_key='test.embedding.skipped',
            summary='',
            title='',
            owner_company_id=self.company.id,
        )
        self.assertEqual(concept.embedding_state, 'skipped')


@tagged('okf', 'post_install', '-at_install')
class TestEmbeddingBackfill(common.TransactionCase):
    """Fas 3.3/3.5: efterfyllnaden ska vara idempotent och sanningsenlig."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.ref('base.main_company')
        cls.Concept = cls.env['ai.okf.concept']
        cls.atype = cls.env['ai.artifact.type'].search(
            [('name', '=', 'knowledge')], limit=1)
        if not cls.atype:
            cls.atype = cls.env['ai.artifact.type'].create({
                'name': 'knowledge', 'kind': 'knowledge',
            })

    def _patch_embedding(self, vector=None):
        """Mocka BÅDE provider-valet och själva anropet.

        `_okf_cron_backfill_embeddings` gör två saker i tur och ordning:
          1. `_embedding_provider()` — väljer provider (returnerar None i en
             test-DB utan `can_embed`-provider, och då avbryter cronen med
             "ingen provider som kan embedda" INNAN `_get_embedding` nås).
          2. `_get_embedding()` — det faktiska HTTP-anropet.

        Att bara mocka (2) räcker inte: cronen faller på (1). Det var
        därför test_backfill_* FAILade i ren nyinstallation (FYND 2026-09-23).
        """
        from contextlib import ExitStack
        vec = vector if vector is not None else [0.1] * 1024
        # OBS: providern måste vara TRUTHY. `browse()` utan id är ett tomt
        # recordset = falsy, och cronen gör `if not provider: return 0` —
        # mocken skulle då tysta testet i stället för att driva det.
        # En riktig (men overifierad) provider-rad duger: _get_embedding
        # är ändå mockad, så ingen HTTP-trafik sker.
        provider = self.env['ai.provider'].create({
            'name': 'Test-embedding-provider',
            'provider_type': 'bifrost',
            'can_embed': True,
            'api_key': 'test-key',
        })
        stack = ExitStack()
        stack.enter_context(patch.object(
            type(self.env['ai.provider']), '_embedding_provider',
            return_value=provider))
        stack.enter_context(patch.object(
            type(self.env['ai.provider']), '_get_embedding',
            return_value=vec))
        return stack

    def test_backfill_fills_pending_and_clears_marker(self):
        """En 'pending'-rad ska få en NY VERSION med vektor (fas 3.5).

        Koncept-rader är ADD-only — därför skapas en ny version istället
        för att skriva in vektorn i den gamla raden. Den gamla blir
        'superseded' och den nya bär vektorn.
        """
        concept = self.Concept.create({
            'artifact_type_id': self.atype.id,
            'scope': 'company',
            'concept_key': 'test.backfill.fills',
            'version': 1,
            'title': 'Backfill-koncept',
            'summary': 'Text som ska vektoriseras av cron',
            'owner_company_id': self.company.id,
            'embedding_state': 'pending',
        })

        with self._patch_embedding([0.5] * 1024):
            filled = self.env['ai.okf.concept']._okf_cron_backfill_embeddings(
                batch_size=50)

        self.assertGreaterEqual(filled, 1)
        # Ny version skapad med vektorn — immutabiliteten bevarad
        versions = self.Concept.search([
            ('concept_key', '=', 'test.backfill.fills'),
            ('scope', '=', 'company'),
        ], order='version desc')
        self.assertEqual(len(versions), 2, 'förväntade två versioner')
        newest = versions[0]
        self.assertEqual(newest.version, 2)
        self.assertIsNotNone(newest.embedding)
        self.assertEqual(newest.embedding_state, 'ready')
        # Den gamla raden är superseded, inte ändrad
        self.assertEqual(versions[1].status, 'superseded')
        self.assertFalse(versions[1].embedding)

    def test_backfill_does_not_touch_ready_rows(self):
        """En 'ready'-rad ska aldrig skapa en ny version (fas 3.3).

        Immutabilitet + ADD-only gör detta extra viktigt: en felaktig
        efterfyllnad skulle annars skapa oändliga versioner av oförändrade
        koncept.
        """
        self.Concept.create({
            'artifact_type_id': self.atype.id,
            'scope': 'company',
            'concept_key': 'test.backfill.idempotent',
            'version': 1,
            'title': 'Redan klar',
            'summary': 'Ska inte röras',
            'owner_company_id': self.company.id,
            'embedding_state': 'ready',
            'embedding': [0.2] * 1024,
        })

        before = self.Concept.search_count([
            ('concept_key', '=', 'test.backfill.idempotent')])
        with self._patch_embedding([0.9] * 1024):
            self.env['ai.okf.concept']._okf_cron_backfill_embeddings(batch_size=50)
        after = self.Concept.search_count([
            ('concept_key', '=', 'test.backfill.idempotent')])
        self.assertEqual(before, after, 'ingen ny version av en klar rad')

    def test_backfill_leaves_pending_when_provider_fails(self):
        """Provider utan svar → raden förblir 'pending' och ingen ny version.

        Skillnaden är viktig: 'pending' betyder "försök igen", 'failed'
        betyder "något är fel, titta på det". Och eftersom rader är
        ADD-only får ett misslyckande ALDRIG skapa en tom version.
        """
        concept = self.Concept.create({
            'artifact_type_id': self.atype.id,
            'scope': 'company',
            'concept_key': 'test.backfill.stays_pending',
            'version': 1,
            'title': 'Försök igen',
            'summary': 'Providern svarar inte just nu',
            'owner_company_id': self.company.id,
            'embedding_state': 'pending',
        })

        with patch.object(type(self.env['ai.provider']), '_get_embedding',
                          return_value=None):
            self.env['ai.okf.concept']._okf_cron_backfill_embeddings(batch_size=50)

        concept.invalidate_recordset()
        self.assertEqual(concept.embedding_state, 'pending')
        self.assertFalse(concept.embedding)
        # Ingen ny version skapades av ett misslyckat försök
        self.assertEqual(
            self.Concept.search_count([
                ('concept_key', '=', 'test.backfill.stays_pending')]), 1)

    def test_backfill_marks_skipped_for_empty_text(self):
        """Rad utan text → 'skipped' (inte evig 'pending'-loop)."""
        concept = self.Concept.create({
            'artifact_type_id': self.atype.id,
            'scope': 'company',
            'concept_key': 'test.backfill.empty_text',
            'version': 1,
            'title': '',
            'summary': '',
            'owner_company_id': self.company.id,
            'embedding_state': 'pending',
        })

        with self._patch_embedding([0.1] * 1024):
            self.env['ai.okf.concept']._okf_cron_backfill_embeddings(batch_size=50)

        concept.invalidate_recordset()
        self.assertEqual(concept.embedding_state, 'skipped')
