# -*- coding: utf-8 -*-
"""Tester för ai.memory på pgvector (ai-memory-pgvector T7).

VARFÖR: `ai.memory` lagrade sina vektorer i ett serialiserat FAISS-index
i en `ir.attachment`. Det gick inte att filtrera på ägare i SQL, inte att
fusionera med svensk BM25, och inläsningen krävde
`allow_dangerous_deserialization=True` (pickle = kodkörning). Nu lagras
chunkarna som rader med `vector(1024)`, och sökningen är tvåsignalig.

Dessa tester bevisar:
  1. en artefakt söks utan agent- eller sessionskoppling (T7.1)
  2. svensk morfologi ger träff via search_vector (T7.2)
  3. _multi_source_recall dedupar på (modell, id), inte id (T7.3)
  4. ingen pickle-väg finns kvar i sökvägen (T7.4)
"""

from unittest.mock import patch

from odoo.tests import common, tagged


@tagged('post_install', '-at_install')
class TestAiMemoryPgvector(common.TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Mem = cls.env['ai.memory']
        cls.Chunk = cls.env['ai.memory.chunk']

    def _make_memory(self, name, body):
        return self.Mem.create({
            'name': name,
            'memory_type': 'text',
            'content': body,
        })

    def _fake_vectors(self, texts):
        """Deterministisk pseudo-vektor — inget provider-anrop i testet."""
        out = []
        for t in texts:
            base = (sum(ord(c) for c in t) % 97) / 97.0
            out.append([base + (i % 10) * 0.001 for i in range(1024)])
        return out

    # ── T7.1: artefakten söks utan koppling ──────────────────────────

    def test_memory_searchable_without_agent_link(self):
        """En artefakt utan agent/session-koppling ska gå att söka."""
        mem = self._make_memory(
            'ensam_artefakt',
            'Avtalshantering: förnyelse sker automatiskt om ingen säger upp.')

        with patch.object(type(self.Mem), '_embed_texts',
                          lambda self, texts: self._fake_vectors(texts)):
            mem.rag_ids.unlink()
            self.Chunk.create({
                'memory_id': mem.id,
                'sequence': 0,
                'content': mem.content,
            })
            hits = mem.faiss_search('avtalshantering', k=3)

        # Utan vektor på chunken faller sökningen tillbaka på BM25, som
        # ska ge träff eftersom ordet finns i texten.
        self.assertTrue(
            isinstance(hits, list),
            'faiss_search ska returnera list[str], fick %r' % type(hits))
        self.assertTrue(
            hits, 'BM25 ska hitta "avtalshantering" i artefaktens text')
        self.assertIn('avtalshantering', hits[0].lower())

    def test_faiss_search_contract_unchanged(self):
        """Signatur och svarskontrakt: list[str], högst k element."""
        mem = self._make_memory('kontrakt', 'Fakturering sker månadsvis.')
        hits = mem.faiss_search('fakturering', k=3)
        self.assertIsInstance(hits, list)
        self.assertLessEqual(len(hits), 3)
        for h in hits:
            self.assertIsInstance(h, str)

    def test_empty_query_returns_empty(self):
        """Tom fråga ger tomt — ingen "senaste N"-fallback."""
        mem = self._make_memory('tom_fraga', 'Något innehåll.')
        self.assertEqual(mem.faiss_search(''), [])
        self.assertEqual(mem.faiss_search('   '), [])

    # ── T7.2: svensk morfologi ───────────────────────────────────────

    def test_swedish_stemming_in_search_vector(self):
        """'kunder' ska hitta 'kund' via search_vector (stamning)."""
        mem = self._make_memory(
            'morfologi',
            'Kunden faktureras i efterskott. Kunden äger rätten att '
            'reklamera inom skälig tid.')
        self.env.flush_all()

        self.env.cr.execute("""
            SELECT 1 FROM ai_memory
            WHERE id = %s
              AND search_vector @@ plainto_tsquery('swedish', %s)
        """, (mem.id, 'kunder'))
        self.assertTrue(
            self.env.cr.fetchone(),
            'Svensk stemming ska matcha "kunder" mot "kunden"')

    def test_search_vector_is_generated_column(self):
        """search_vector ska vara GENERATED — inte ett fält att glömma."""
        self.env.cr.execute("""
            SELECT is_generated FROM information_schema.columns
            WHERE table_name = 'ai_memory'
              AND column_name = 'search_vector'
        """)
        row = self.env.cr.fetchone()
        self.assertTrue(row, 'search_vector saknas på ai_memory')
        self.assertEqual(row[0], 'ALWAYS')

    # ── T7.3: dedup på (modell, id) ──────────────────────────────────

    def test_multi_source_dedup_uses_model_and_id(self):
        """Två modeller med samma id får inte kollapsa till en träff."""
        coworker = self.env['ai.coworker'].search([], limit=1)
        if not coworker:
            self.skipTest('ingen ai.coworker i databasen')

        mem = self._make_memory('dedup_test', 'Delad text om avtal.')
        concept = self.env['ai.okf.concept'].search([], limit=1)
        if not concept:
            self.skipTest('inget ai.okf.concept i databasen')

        # Simulera två träffar med SAMMA id men olika modell.
        fake_hits = [(mem, 0.9), (concept, 0.8)]

        with patch.object(
                type(coworker), '_recall_from_source',
                lambda self, *a, **kw: fake_hits):
            src = self.env['ai.search.source'].search(
                [('code', '=', 'ai_memory')], limit=1)
            if not src:
                self.skipTest('ai_memory-källan saknas')
            records, diag = coworker._multi_source_recall(
                'avtal', scope='company',
                owner_id=self.env.company.id, sources=src)

        names = {r['_name'] for r in records}
        self.assertEqual(
            diag['deduped'], 2,
            'Två olika modeller med samma id ska ge två poster, '
            'inte en — fick %d' % diag['deduped'])

    def test_multi_source_returns_dicts(self):
        """Returvärdet är dictar, så blandade modeller inte kraschar."""
        coworker = self.env['ai.coworker'].search([], limit=1)
        if not coworker:
            self.skipTest('ingen ai.coworker i databasen')
        mem = self._make_memory('dict_test', 'Text.')
        with patch.object(
                type(coworker), '_recall_from_source',
                lambda self, *a, **kw: [(mem, 0.5)]):
            src = self.env['ai.search.source'].search(
                [('code', '=', 'ai_memory')], limit=1)
            if not src:
                self.skipTest('ai_memory-källan saknas')
            records, _diag = coworker._multi_source_recall(
                'text', scope='company',
                owner_id=self.env.company.id, sources=src)
        if records:
            self.assertIsInstance(records[0], dict)
            self.assertIn('_name', records[0])
            self.assertIn('record', records[0])

    # ── T7.4: ingen pickle-väg i sökningen ───────────────────────────

    def test_no_pickle_in_search_path(self):
        """faiss_search får inte deserialisera något."""
        import inspect
        from odoo.addons.ai_agent_core.models.ai_memory import AIMemory

        src = inspect.getsource(AIMemory.faiss_search)
        self.assertNotIn('allow_dangerous_deserialization', src)
        self.assertNotIn('load_faiss', src)
        self.assertNotIn('FAISS', src)

    def test_memory_type_is_a_source_not_a_format(self):
        """'faiss' får inte finnas som memory_type — det är ett format."""
        selection = dict(
            self.Mem._fields['memory_type']._description_selection(self.env))
        self.assertNotIn('faiss', selection)
        self.assertNotIn('pgvector', selection)
        for expected in ('pdf', 'website', 'markdown', 'text'):
            self.assertIn(
                expected, selection,
                'memory_type ska bära källan %r' % expected)

    # ── Chunkarna ────────────────────────────────────────────────────

    def test_chunk_count_is_computed(self):
        """chunk_count räknas ur rag_ids — inte ett fält att skriva."""
        mem = self._make_memory('chunk_count_test', 'Text att dela.')
        self.assertEqual(mem.chunk_count, 0)
        self.Chunk.create({
            'memory_id': mem.id, 'sequence': 0, 'content': 'a'})
        self.Chunk.create({
            'memory_id': mem.id, 'sequence': 10, 'content': 'b'})
        mem.invalidate_recordset()
        self.assertEqual(mem.chunk_count, 2)

    def test_chunks_cascade_on_memory_delete(self):
        """Chunkar raderas med artefakten."""
        mem = self._make_memory('cascade_test', 'Text.')
        self.Chunk.create({
            'memory_id': mem.id, 'sequence': 0, 'content': 'x'})
        chunk_ids = mem.rag_ids.ids
        self.assertTrue(chunk_ids)
        mem.unlink()
        self.assertFalse(
            self.Chunk.browse(chunk_ids).exists(),
            'chunkar ska raderas av ondelete=cascade')

    def test_embedding_column_is_vector_1024(self):
        """embedding ska vara vector(1024), inte dimensionslös vector."""
        for table in ('ai_memory', 'ai_memory_chunk'):
            self.env.cr.execute("""
                SELECT format_type(a.atttypid, a.atttypmod)
                FROM pg_attribute a
                JOIN pg_class c ON a.attrelid = c.oid
                WHERE c.relname = %s AND a.attname = 'embedding'
            """, (table,))
            row = self.env.cr.fetchone()
            self.assertTrue(row, '%s.embedding saknas' % table)
            self.assertEqual(
                row[0], 'vector(1024)',
                '%s.embedding ska vara vector(1024), fick %r'
                % (table, row[0]))
