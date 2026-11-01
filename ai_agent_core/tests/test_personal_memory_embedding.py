# -*- coding: utf-8 -*-
"""B11 — personal memory: `input_type` och `model` måste skickas.

Bakgrund: `_embed_text` anropade `provider._get_embedding(input=text)` utan
`model` och utan `input_type`. Två konsekvenser:

  1. Modeller som KRÄVER `input_type` svarar inte alls — gatewayen hänger i
     ~45 s och ger tyst `None` (mätt 2026-09-14 i `_embedding_payload`).
     OKF-vägen skickar `input_type='search_document'` och fungerar.
  2. Utan `model` föll anropet tillbaka på `DEFAULT_EMBEDDING_MODEL`
     ('text-embedding-3-small') — den modell Bifrost avvisar med 401.
     Providerns eget `embedding_model` hoppades alltså över.

Följden var att personliga minnen saknade vektorer → den semantiska signalen
var död och bara BM25 återstod.

Testet använder en fejkad provider som REGISTRERAR vilka parametrar den får.
Det är rätt nivå: vi bevakar kontraktet mot providern, inte HTTP-anropet.
"""

from unittest.mock import patch

from odoo.tests.common import TransactionCase, tagged


class _RecordingProvider:
    """Provider-stub som spelar in anropet och ger en giltig vektor."""

    def __init__(self, dim=1024):
        self.calls = []
        self._dim = dim
        self.embedding_model = 'stub-embed-model'

    def _effective_embedding_model(self, model=None):
        return model or self.embedding_model

    def _get_embedding(self, model=None, input=None, input_type=None):
        self.calls.append({
            'model': model, 'input_type': input_type,
            'input': (input or '')[:40],
        })
        return [0.01] * self._dim

    def _get_embedding_batch(self, model=None, inputs=None, input_type=None):
        self.calls.append({
            'model': model, 'input_type': input_type,
            'batch': len(inputs or []),
        })
        return [[0.01] * self._dim for _ in (inputs or [])]


@tagged('ai_core', 'post_install', '-at_install')
class TestEmbeddingParameters(TransactionCase):
    """16.1/16.2: rätt parametrar — och rätt `input_type` per användning."""

    def setUp(self):
        super().setUp()
        self.Memory = self.env['ai.personal.memory']

    def _patch_provider(self, provider):
        return patch.object(
            type(self.env['ai.provider']), '_embedding_provider',
            lambda self_: provider)

    def test_document_text_uses_search_document(self):
        provider = _RecordingProvider()
        with self._patch_provider(provider):
            vector = self.Memory._embed_text('ett minne att spara')
        self.assertTrue(vector, 'vektorn ska skapas')
        self.assertEqual(len(provider.calls), 1)
        call = provider.calls[0]
        self.assertEqual(
            call['input_type'], 'search_document',
            'B11: ett minne är ett DOKUMENT — input_type måste skickas')
        self.assertEqual(
            call['model'], 'stub-embed-model',
            'B11: providerns eget embedding_model ska användas, inte '
            'DEFAULT_EMBEDDING_MODEL')

    def test_query_text_uses_search_query(self):
        provider = _RecordingProvider()
        with self._patch_provider(provider):
            self.Memory._generate_embedding(
                'hur bokför jag moms?', input_type='search_query')
        self.assertEqual(provider.calls[0]['input_type'], 'search_query',
                         'en sökfråga ska embeddas som fråga, inte dokument')

    def test_search_path_uses_search_query(self):
        """Sökvägen får inte skicka search_document för en fråga.

        Vi läser källkoden i stället för att anropa `search_for_user`:
        den sätter `_embedding_is_vector_cache` som ett klassattribut, och
        Odoos attributkontroll slår då larm när ett senare test rör samma
        modell. Kontraktet är detsamma — anropet ska bära 'search_query'.
        """
        import os
        from odoo.addons.ai_agent_core import __file__ as pkg_file

        path = os.path.join(os.path.dirname(pkg_file), 'models',
                            'ai_personal_memory.py')
        with open(path, encoding='utf-8') as fh:
            src = fh.read()

        marker = 'query_embedding = self._generate_embedding('
        idx = src.find(marker)
        self.assertNotEqual(idx, -1, 'sökvägens embedding-anrop hittades inte')
        snippet = src[idx:idx + 200]
        self.assertIn(
            "input_type='search_query'", snippet,
            'B11: sökvägen ska embedda frågan som search_query, inte '
            'search_document')

    def test_batch_path_sends_model_and_input_type(self):
        provider = _RecordingProvider()
        with self._patch_provider(provider):
            self.Memory.embed_batch(['a', 'b'])
        call = provider.calls[0]
        self.assertEqual(call['input_type'], 'search_document')
        self.assertEqual(call['model'], 'stub-embed-model')
        self.assertEqual(call['batch'], 2)

    def test_no_provider_returns_none_without_raising(self):
        with self._patch_provider(None):
            self.assertIsNone(self.Memory._embed_text('text'))


@tagged('ai_core', 'post_install', '-at_install')
class TestBackfill(TransactionCase):
    """16.4: backfill fyller på rader som saknar vektor."""

    def setUp(self):
        super().setUp()
        self.Memory = self.env['ai.personal.memory']

    def _memory(self, content):
        return self.Memory.create({
            'user_id': self.env.user.id,
            'content': content,
            'category': 'fact',
            'source': 'manual',
        })

    def test_backfill_fills_missing_vectors(self):
        provider = _RecordingProvider()
        mem = self._memory('minne utan vektor')
        self.assertFalse(mem.embedding)

        with patch.object(
                type(self.env['ai.provider']), '_embedding_provider',
                lambda self_: provider):
            result = self.Memory._backfill_missing_embeddings()

        self.assertGreaterEqual(result['filled'], 1)
        mem.invalidate_recordset()
        self.assertTrue(mem.embedding, 'backfill ska ha satt en vektor')

    def test_backfill_is_idempotent(self):
        provider = _RecordingProvider()
        self._memory('minne utan vektor')
        with patch.object(
                type(self.env['ai.provider']), '_embedding_provider',
                lambda self_: provider):
            self.Memory._backfill_missing_embeddings()
            second = self.Memory._backfill_missing_embeddings()
        self.assertEqual(second['found'], 0,
                         'andra körningen ska inte hitta något att fylla')

    def test_backfill_survives_batch_failure(self):
        class _BrokenProvider(_RecordingProvider):
            def _get_embedding_batch(self, model=None, inputs=None,
                                     input_type=None):
                raise RuntimeError('provider nere')

        self._memory('minne utan vektor')
        with patch.object(
                type(self.env['ai.provider']), '_embedding_provider',
                lambda self_: _BrokenProvider()):
            result = self.Memory._backfill_missing_embeddings()
        self.assertGreaterEqual(result['failed'], 1)
        self.assertEqual(result['filled'], 0)
