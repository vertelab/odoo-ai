# -*- coding: utf-8 -*-
"""ai.memory — FAISS/pgvector memory for agents."""

import logging
import os

from odoo import models, fields, api

from ..fields.pg_vector import PgVector
from .ai_okf_concept import EMBEDDING_DIM as _OKF_EMBEDDING_DIM

_logger = logging.getLogger(__name__)

# Lägsta cosine-likhet för en chunktraff. Utan tröskel returnerar
# pgvector alltid de närmaste grannarna — även för en fråga utan
# semantisk koppling.
#
# Tröskeln är EMPIRISKT satt, inte kopierad. Mätt 2026-10-30 mot
# mistral/mistral-embed (providern i drift, inte OpenAI):
#
#   relevant fråga    ('avtal', 'uppsägningstid')  0.71 – 0.83
#   irrelevant fråga  ('obegripligtord', 'xyzzy')  0.59 – 0.65
#
# Modellen ger alltså hög baslikhet för allt — 0.1 (ai.memory.mixins
# default, kalibrerad för en annan modell) släppte igenom precis allt.
# 0.70 ligger i glappet mellan de två grupperna.
#
# OBS: byts embeddingmodell måste denna kalibreras om. Den är en
# egenskap hos MODELLEN, inte hos koden.
_CHUNK_MIN_COSINE = 0.70

# ── OpenMP-trådar: faiss segfaultar annars i worker-kontext ─────────────
# faiss-cpu (pip-hjul) bär SIN EGEN libgomp + OpenBLAS
# (faiss_cpu.libs/libgomp-*.so, libopenblaso-*.so), medan numpy bär sin
# (numpy.libs/libscipy_openblas64_*.so) och Odoo/gevent har egna trådar.
# Två OpenMP-runtimes i samma process startar två trådpooler; vid import
# av faiss i en Odoo-worker kolliderar de och processen dör på C-nivå:
#
#   Fatal Python error: Segmentation fault
#     faiss/loader.py:187  from .swigfaiss import *
#     faiss/swigfaiss.py:10  from . import _swigfaiss   ← kraschen
#
# Symptom (2026-10-29): /ai/stream?session_id=<n> gav 502 i Caddy och
# "Empty reply from server" direkt mot :8069 — workern dog tyst, och
# inget hamnade i odoo-server.log eftersom kraschen sker under import i
# C-lagret. UTAN session_id gick det bra, eftersom _get_quest_memories()
# då aldrig nådde faiss_search(). Samma anrop fungerade i `odoo shell`
# (annan process, annan tråd-/biblioteksordning) — därför svårt att se.
#
# OMP_NUM_THREADS=1 tvingar faiss till en enda tråd: ingen pool-konflikt,
# ingen segfault. Måste sättas INNAN faiss importeras — dvs. här, vid
# modulimport, inte i load_faiss(). setdefault respekterar ett värde som
# redan satts i processmiljön (t.ex. systemd-drop-in).
for _var in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS'):
    os.environ.setdefault(_var, '1')


class AIMemory(models.Model):
    _name = 'ai.memory'
    _description = 'AI Memory'
    _order = 'create_date desc'

    # OKF-FORMATET, inte indexeringskontraktet (ai-memory-okf-format).
    #
    # ai.memory ärver ai.okf.format.mixin — okf_body, okf_summary,
    # okf_links — men INTE ai.okf.mixin, som bär okf_dirty och
    # okf_indexed_at. memory-specen förbjuder dem på artefakten: den är
    # material, inte inlärning, och ska inte indexeras till
    # ai.okf.concept av någon cron.
    #
    # Att regeln nu är STRUKTURELL (fältet finns inte) i stället för en
    # kommentar är hela poängen med delningen.
    _inherit = ['ai.okf.format.mixin']

    # OKF-taggar: egen relationstabell (en many2many kan inte ligga
    # på en abstrakt mixin — den ger samma tabell för alla ärvande).
    # Tabellnamnet är oförändrat sedan tidigare, så inga data flyttas.
    okf_tags = fields.Many2many(
        'ai.okf.tag', 'ai_memory_okf_tag_rel', 'res_id', 'tag_id',
        string='OKF Tags')

    name = fields.Char('Memory Key')

    # `content` är ett BAKÅTKOMPATIBELT ALIAS för okf_body
    # (ai-memory-okf-format D4). Fältet läses på flera ställen
    # (controllers/stream.py, ai_coworker.py, ai_memory_mixin.py) och
    # byttes inte ut rakt av — ett compute/inverse-alias kostar noll
    # anropare och kan tas bort när inget läser det längre.
    content = fields.Text(
        'Content', compute='_compute_content',
        inverse='_inverse_content', search='_search_content',
        help='Alias för okf_body — behålls för bakåtkompatibilitet.')

    @api.depends('okf_body')
    def _compute_content(self):
        for rec in self:
            rec.content = rec.okf_body

    def _inverse_content(self):
        for rec in self:
            rec.okf_body = rec.content

    def _search_content(self, operator, value):
        return [('okf_body', operator, value)]
    # memory_type betecknar artefaktens KÄLLA, inte dess lagringsformat
    # (ai-memory-pgvector T6.1). Ett värde som 'faiss' beskrev en
    # implementation och togs bort — lagringen är nu alltid pgvector.
    memory_type = fields.Selection([
        ('pdf', 'PDF'),
        ('website', 'Webbplats'),
        ('markdown', 'Markdown'),
        ('text', 'Plain Text'),
        ('model', 'Odoo-modell'),
        ('attachments', 'Bilagor'),
        ('youtube', 'YouTube'),
    ], default='text',
        help='Var innehållet kom ifrån. Lagringsformatet är alltid '
             'pgvector och styrs inte härifrån.')

    # Embedding
    embedding_model = fields.Char('Embedding Model')
    embedding_vector = fields.Text('Vector (base64)')

    # Relations
    identity_id = fields.Many2one('ai.identity', string='Identity')
    agent_id = fields.Many2one('ai.agent', string='Agent')

    # Quest learning (quest-learning-memory)
    quest_id = fields.Many2one('ai.coworker', string='Quest',
                                help='The quest this memory belongs to')
    category = fields.Selection([
        ('preference', 'User Preference'),
        ('fact', 'Key Fact'),
        ('correction', 'Correction'),
        ('pattern', 'Pattern'),
        ('feedback', 'Feedback'),
    ], string='Category')
    session_id = fields.Many2one('ai.coworker.session', string='Session',
                                   help='Session this memory belongs to (for session-level RAG)')
    source_thread_id = fields.Many2one('ai.coworker.session', string='Source Thread',
                                        help='Thread where this memory was extracted')
    consolidated = fields.Boolean('Consolidated', default=False,
                                   help='Included in system prompt after consolidation')
    archived = fields.Boolean('Archived', default=False,
                               help='Hidden from system prompt injection')

    # ── OKF: INGEN koppling (okf-mixin, 2026-09-22) ────────────────────
    # `ai.memory` är agentens RAG-kapacitet: vektoriserade PDF:er,
    # `faiss_search`, `rag_memory_ids`. Det är material en agent ARBETAR
    # med — inte kunskap som ska bli OKF-koncept.
    #
    # Modellen bar tidigare `okf_dirty` + `okf_indexed_at` +
    # `artifact_type_id` och indexerades till `ai.okf.concept`. Det var ett
    # kategorifel: en uppladdad PDF blev både FAISS-index OCH
    # kunskapskoncept, och en dirty-flagga skapade en rad per cron-varv.
    # `ai-memory`-specen säger redan att modellen är "material, inte
    # inlärning" — nu gör koden detsamma.
    #
    # Vägen från uppladdat material till koncept går via `ai.okf.upload`,
    # som skriver konceptet direkt ur `ir.attachment`. RAG-funktionerna
    # nedan är orörda.

    # Metadata
    tags = fields.Char('Tags', help='Comma-separated')
    importance = fields.Selection([
        ('low', 'Low'), ('medium', 'Medium'), ('high', 'High'),
    ], default='medium')

    # Timestamps
    last_accessed = fields.Datetime()
    access_count = fields.Integer(default=0)

    # ── Vektorer (ai-memory-pgvector) ──
    # Samma mönster som ai.okf.concept: en riktig pgvector-kolumn med
    # dimension, inte en serialiserad FAISS-blob i en bilaga. Kolumnen
    # sätts till vector(1024) av hooks.py (memory_ensure_search_infrastructure)
    # EFTER att tabellen skapats — en migration som kör ALTER TABLE före
    # tabellen finns svalde felet i migration 1.11 och loggade "klart"
    # medan kolumnen aldrig uppstod.
    embedding = PgVector(
        string='Embedding', dimension=_OKF_EMBEDDING_DIM,
        help='pgvector(%d) embedding av okf_body. Kolumnen är vector(%d)-typ.'
             % (_OKF_EMBEDDING_DIM, _OKF_EMBEDDING_DIM))

    # search_vector — GENERATED COLUMN, skapad av SQL i hooks.py.
    # Är avsiktligt INTE ett ORM-fält: en genererad kolumn ska inte kunna
    # glömmas bort av skrivsidan (samma skäl som i ai_okf_concept).
    #   to_tsvector('swedish', coalesce(okf_body,'') || ' ' || coalesce(name,''))

    # ── Legacy: FAISS-index ──
    # Fältet är KVAR men är inte längre en sökväg. Det behövs av
    # re-embedding-cronen: de artefakter som skapades före
    # pgvector-övergången har ingen source_attachment_id och ibland ingen
    # content — deras enda kvarvarande text ligger i FAISS-bloben
    # (extract_legacy_faiss_text). Nya artefakter får aldrig detta fält
    # satt; create_vector skriver chunkar i stället.
    faiss_attachment_id = fields.Many2one(
        'ir.attachment', string='Legacy FAISS Index', readonly=True,
        help='Serialiserat FAISS-index från tiden före pgvector. Läses '
             'bara av re-embedding-cronen — används aldrig för sökning.')
    source_attachment_id = fields.Many2one('ir.attachment',
        string='Source File',
        help='The original uploaded file (ir.attachment) this memory '
             'was created from. Used by the chat UI to render a '
             'download link.')
    chunk_count = fields.Integer(
        'Chunk Count', compute='_compute_chunk_count', store=True,
        help='Antal chunkar i artefakten. Räknas ur rag_ids — inte ett '
             'fält någon måste komma ihåg att skriva.')

    # ── Chunkar (ai-memory-pgvector) ──
    rag_ids = fields.One2many(
        'ai.memory.chunk', 'memory_id', string='Chunks',
        help='Chunkar av artefaktens text. Ersätter det serialiserade '
             'FAISS-indexet.')

    @api.depends('rag_ids')
    def _compute_chunk_count(self):
        for rec in self:
            rec.chunk_count = len(rec.rag_ids)

    # ── Vektorlagring (pgvector) ──

    def _split_documents(self, documents):
        """Dela dokument i chunkar.

        `add_start_index=True` bevaras: start_index är det enda som låter
        oss citera tillbaka till rätt ställe i källan — utan det blir en
        chunk en lösryckt mening.

        Args:
            documents: list[langchain Document]
        Returns:
            list[langchain Document]
        """
        if not documents:
            return []
        from langchain_text_splitters import RecursiveCharacterTextSplitter
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=1000,
            chunk_overlap=200,
            add_start_index=True,
        )
        return splitter.split_documents(documents)

    def create_vector(self, documents: list, embedding_model_name: str = ''):
        """Dela, embedda och lagra som ai.memory.chunk (pgvector).

        Ersätter det serialiserade FAISS-indexet (ai-memory-pgvector).
        Chunkarna blir egna rader med vector(1024)-kolumn — sökningen kan
        då filtrera på ägare i SQL och fusioneras med svensk BM25.

        Args:
            documents: List of langchain Document objects
            embedding_model_name: Name of the embedding model used

        Returns:
            Number of chunks created
        """
        self.ensure_one()
        if not documents:
            return 0

        chunks = self._split_documents(documents)
        if not chunks:
            return 0

        texts = [d.page_content for d in chunks]
        vectors = self._embed_texts(texts)

        Chunk = self.env['ai.memory.chunk']
        # Ersätt tidigare chunkar — create_vector är en omskrivning av
        # artefaktens innehåll, inte en påfyllning.
        self.rag_ids.unlink()

        vals_list = []
        for seq, (doc, vec) in enumerate(zip(chunks, vectors)):
            vals = {
                'memory_id': self.id,
                'sequence': seq * 10,
                'content': doc.page_content,
                'start_index': (doc.metadata or {}).get('start_index', 0),
                'metadata': doc.metadata or {},
            }
            if vec:
                vals['embedding'] = vec
            vals_list.append(vals)

        Chunk.create(vals_list)

        # Förälderns egen vektor: artefakten ska gå att hitta även när
        # ingen chunk matchar (t.ex. en kort sammanfattning).
        #
        # OBS: skrivs via write() med STRÄNGEN, inte `self.embedding = vec`.
        # PgVector läser tillbaka som numpy-array, och Odoos write-cache
        # jämför `val != value` — det ger "The truth value of an array with
        # more than one element is ambiguous" (mätt 2026-10-30).
        body = self._memory_body_text()
        vals = {'embedding_model': embedding_model_name or 'bifrost-default'}
        if body:
            parent_vec = self._embed_texts([body[:8192]])[0]
            if parent_vec:
                vals['embedding'] = parent_vec

        self.write(vals)

        _logger.info(
            'pgvector: %d chunkar för minne %s (%d med vektor)',
            len(chunks), self.name, sum(1 for v in vectors if v))
        return len(chunks)

    def _memory_body_text(self):
        """Artefaktens text för embedding.

        Läser `okf_body` DIREKT — inte `content`. Sedan `content` blev ett
        compute/inverse-alias för `okf_body` skulle `self.content` här
        skapa en cirkel (compute läser okf_body, som denna metod läste via
        content).
        """
        self.ensure_one()
        return self.okf_body or ''

    def _embed_texts(self, texts):
        """Embedda en lista texter via ai.provider (batch).

        Returnerar list[list[float]|None]. LISTA, inte pgvector-literal:
        PgVector-fältets convert_to_cache ger annars
        "The truth value of an array with more than one element is
        ambiguous" när Odoos write-cache jämför värdet (mätt 2026-10-30).

        Saknas provider loggas en varning — chunkarna sparas utan vektor
        (BM25 fungerar ändå), aldrig en tyst nedgradering av memory_type.
        """
        if not texts:
            return []
        Provider = self.env['ai.provider'].sudo()
        provider = Provider._embedding_provider()
        if not provider:
            _logger.warning(
                'Embedding: ingen provider kan skapa vektorer — '
                '%d chunkar sparas utan vektor (BM25-only)', len(texts))
            return [None] * len(texts)

        out = []
        batch_size = 100
        for i in range(0, len(texts), batch_size):
            batch = [t[:8192] for t in texts[i:i + batch_size]]
            try:
                embs = provider._get_embedding_batch(
                    inputs=batch, input_type='search_document')
            except Exception as exc:
                _logger.warning(
                    'Embedding (batch %d-%d) misslyckades: %s',
                    i, i + len(batch), exc)
                embs = None
            if not embs:
                out.extend([None] * len(batch))
                continue
            for emb in embs:
                if emb and isinstance(emb, (list, tuple)):
                    out.append(list(emb))
                else:
                    out.append(None)
        return out

    def load_faiss(self):
        """Läs ett LEGACY FAISS-index ur en bilaga — endast för utdrag.

        Behövs av re-embedding-cronen (ai_memory_reembed): de artefakter
        som skapades före ai-memory-pgvector har ingen `source_attachment_id`
        och ibland ingen `content` — deras enda kvarvarande text ligger i
        FAISS-bloben. Utan denna väg skulle de bli osökbara.

        Används INTE av sökvägen längre (faiss_search söker pgvector).

        Returns:
            FAISS vector store or None if not available
        """
        self.ensure_one()
        if not self.faiss_attachment_id:
            return None

        try:
            import base64 as b64
            from langchain_community.vectorstores import FAISS

            # 10.5 (okf-recall-path): pickle-deserialisering är kodkörning.
            # `allow_dangerous_deserialization=True` krävs av FAISS-formatet,
            # men får bara ske på en bilaga som VI skapade — annars kan en
            # uppladdad fil köra godtycklig kod vid inläsning. Grinden:
            # bilagan måste vara skapad av systemet (create_uid = root) och
            # ha vårt eget namn. En användaruppladdad pickle avvisas.
            att = self.faiss_attachment_id
            _root = self.env.ref('base.user_root', raise_if_not_found=False)
            if _root and att.create_uid and att.create_uid.id != _root.id:
                _logger.error(
                    'FAISS load avvisad för memory %s: bilagan är inte '
                    'systemskapad (create_uid=%s) — pickle-deserialisering '
                    'skulle köra godtycklig kod.',
                    self.name, att.create_uid.login)
                return None

            data = b64.b64decode(att.datas)
            embeddings = self._get_embeddings()
            if not embeddings:
                return None

            db = FAISS.deserialize_from_bytes(
                data, embeddings,
                allow_dangerous_deserialization=True,
            )
            return db

        except Exception as e:
            _logger.error('FAISS load failed for memory %s: %s', self.name, e)
            return None

    def extract_legacy_faiss_text(self):
        """Dra ut all text ur ett legacy FAISS-index.

        Enda källan för artefakter som varken har `content` eller
        `source_attachment_id`. Returnerar en lista `Document` redo för
        `create_vector`, eller tom lista.

        Returns:
            list[langchain Document]
        """
        self.ensure_one()
        db = self.load_faiss()
        if not db:
            return []
        try:
            from langchain_core.documents import Document
            docs = []
            for _id, doc in (db.docstore._dict or {}).items():
                text = getattr(doc, 'page_content', None)
                if text and text.strip():
                    docs.append(Document(
                        page_content=text,
                        metadata=dict(getattr(doc, 'metadata', {}) or {}),
                    ))
            _logger.info(
                'Legacy FAISS: drog ut %d dokument ur minne %s',
                len(docs), self.name)
            return docs
        except Exception as e:
            _logger.error(
                'Legacy FAISS-utdrag misslyckades för %s: %s', self.name, e)
            return []

    # ── Re-embedding av legacy-artefakter (ai-memory-pgvector T4.3) ──

    def _reembed_documents(self):
        """Hämta artefaktens text som `Document` för re-embedding.

        Ordningen är avsiktlig — den dyraste vägen sist:
          1. `source_attachment_id` (källfilen, om den sparades)
          2. `content` / `okf_body` (texten finns kvar)
          3. legacy FAISS-index (enda kvarvarande källan)

        Returnerar tom lista när ingen text finns — artefakten ska då
        RAPPORTERAS, inte tyst bli en tom rad.

        Returns:
            list[langchain Document]
        """
        self.ensure_one()
        from langchain_core.documents import Document

        # 1. Källfilen
        att = self.source_attachment_id
        if att and att.datas:
            try:
                import base64 as b64
                import io
                raw = b64.b64decode(att.datas)
                if (att.mimetype or '').endswith('pdf'):
                    import pymupdf
                    pdf = pymupdf.open(stream=raw, filetype='pdf')
                    docs = [
                        Document(
                            page_content=page.get_text(),
                            metadata={'page': i + 1, 'source': att.name})
                        for i, page in enumerate(pdf)
                        if page.get_text().strip()
                    ]
                    pdf.close()
                    if docs:
                        return docs
                else:
                    text = raw.decode('utf-8', errors='replace')
                    if text.strip():
                        return [Document(
                            page_content=text,
                            metadata={'source': att.name})]
            except Exception as exc:
                _logger.warning(
                    'Re-embedding: kunde inte läsa bilaga %s: %s',
                    att.name, exc)

        # 2. Texten på posten
        body = self._memory_body_text()
        if body and body.strip():
            return [Document(
                page_content=body, metadata={'source': 'content'})]

        # 3. Legacy FAISS-indexet
        return self.extract_legacy_faiss_text()

    @api.model
    def _cron_reembed_legacy(self):
        """Re-embedda artefakter som skapades före pgvector-övergången.

        Kör som cron.task (ai_memory_reembed). Idempotent: hoppar över
        artefakter som redan har chunkar.

        FYND (mätt mot driftdata 2026-10-30): 6 faiss-artefakter, varav
        3 hade `content` och 3 varken `content` eller
        `source_attachment_id`. Den ursprungliga planen — "re-embedda från
        source_attachment_id" — matchade NOLL rader. Utan FAISS-utdraget
        skulle 3 artefakter tyst bli osökbara.

        Artefakter där ingen text alls kan hittas loggas med namn och id,
        så de syns i stället för att försvinna.
        """
        # Alla utan chunkar, oavsett memory_type: en 'text'-artefakt kan
        # mycket väl ha content som aldrig blev chunkad.
        candidates = self.search([
            ('rag_ids', '=', False),
            ('archived', '=', False),
        ], limit=50)

        done = skipped = 0
        dead = []
        for mem in candidates:
            try:
                docs = mem._reembed_documents()
                if not docs:
                    dead.append('%s (id=%s)' % (mem.name, mem.id))
                    continue
                n = mem.create_vector(docs)
                if n:
                    done += 1
                else:
                    skipped += 1
            except Exception as exc:
                _logger.warning(
                    'Re-embedding misslyckades för %s (id=%s): %s',
                    mem.name, mem.id, exc, exc_info=True)
                skipped += 1

        if done or dead or skipped:
            _logger.info(
                'Re-embedding: %d klara, %d överhoppade, %d utan text',
                done, skipped, len(dead))
        if dead:
            _logger.warning(
                'Re-embedding: ingen text kunde hittas för %s — dessa '
                'artefakter är osökbara tills en källa laddas upp igen',
                ', '.join(dead))
        return done

    def faiss_search(self, query: str, k: int = 3) -> list[str]:
        """Sök i artefaktens chunkar (pgvector).

        Namnet behålls avsiktligt: `search` skulle skugga ORM:ets
        `search(domain)` och göra varje env['ai.memory'].search([...]) fel
        (ai-memory-shadowing-fix). Signatur och svarskontrakt är oförändrade
        — controllers/stream.py:1948 är orörd.

        Söker chunkar när artefakten har sådana; faller tillbaka på
        artefaktens egen vektor (och sist på BM25) när den inte har det.

        Args:
            query: Search query string
            k: Number of results to return

        Returns:
            List of document content strings
        """
        self.ensure_one()
        if not query or not query.strip():
            return []

        try:
            hits = self._search_chunks(query, k=k)
            if hits:
                self.write({
                    'last_accessed': fields.Datetime.now(),
                    'access_count': self.access_count + 1,
                })
                return hits

            # Ingen chunktraff: använd artefaktens egen text om den finns.
            body = self._memory_body_text()
            if body and self._bm25_matches(query):
                self.write({
                    'last_accessed': fields.Datetime.now(),
                    'access_count': self.access_count + 1,
                })
                return [body[:2000]]
            return []

        except Exception as e:
            _logger.error(
                'Chunksökning misslyckades för minne %s: %s', self.name, e)
            return []

    def _bm25_matches(self, query):
        """True om artefaktens search_vector matchar frågan (svensk FTS)."""
        self.ensure_one()
        try:
            with self.env.cr.savepoint():
                self.env.cr.execute("""
                    SELECT 1 FROM ai_memory
                    WHERE id = %s
                      AND search_vector @@ plainto_tsquery('swedish', %s)
                    LIMIT 1
                """, (self.id, query))
                return bool(self.env.cr.fetchone())
        except Exception as exc:
            _logger.warning('BM25-kontroll misslyckades: %s', exc)
            return False

    def _search_chunks(self, query, k=3):
        """Hybrid sökning över chunkar: cosine + svensk BM25.

        Två signaler i en fråga, samma mönster som ai.memory.mixin:s
        _search_memory — men här söks CHUNKAR, inte rader. `limit` avser
        chunkar: en artefakt med 900 chunkar får inte förbruka hela
        budgeten på en enda artefakt.

        Returns:
            list[str] — chunktexter, högst `k` stycken.
        """
        self.ensure_one()
        if not self.rag_ids:
            return []

        vec = None
        try:
            vecs = self._embed_texts([query])
            raw = vecs[0] if vecs else None
            # SQL-parametern ska vara en pgvector-literal, inte en Python-lista
            if raw:
                vec = '[' + ','.join(str(v) for v in raw) + ']'
        except Exception as exc:
            _logger.warning('Fråge-embedding misslyckades: %s', exc)

        scored = {}

        # SIGNAL 1: cosine (pgvector)
        #
        # Tröskeln är nödvändig: utan den returnerar cosine ALLTID de
        # närmaste grannarna, även för en fråga som inte har någon
        # semantisk koppling alls. Mätt 2026-10-30: frågan
        # 'obegripligtord' gav 2 träffar mot en artefakt om avtal.
        # Samma princip som _okf_search (memory-spec rad 87): varje signal
        # får sin egen tröskel i sin egen enhet.
        if vec:
            try:
                with self.env.cr.savepoint():
                    self.env.cr.execute("""
                        SELECT id, content,
                               1 - (embedding <=> %s::vector) AS score
                        FROM ai_memory_chunk
                        WHERE memory_id = %s
                          AND embedding IS NOT NULL
                          AND 1 - (embedding <=> %s::vector) >= %s
                        ORDER BY score DESC
                        LIMIT %s
                    """, (vec, self.id, vec, _CHUNK_MIN_COSINE, k * 4))
                    for row in self.env.cr.dictfetchall():
                        scored[row['id']] = {
                            'content': row['content'],
                            'score': float(row['score'] or 0.0),
                        }
            except Exception as exc:
                _logger.warning(
                    'Cosine-sökning över chunkar misslyckades: %s', exc)

        # SIGNAL 2: svensk BM25 (tsvector)
        try:
            with self.env.cr.savepoint():
                self.env.cr.execute("""
                    SELECT id, content,
                           ts_rank(search_vector,
                                   plainto_tsquery('swedish', %s)) AS score
                    FROM ai_memory_chunk
                    WHERE memory_id = %s
                      AND search_vector @@ plainto_tsquery('swedish', %s)
                    ORDER BY score DESC
                    LIMIT %s
                """, (query, self.id, query, k * 4))
                for row in self.env.cr.dictfetchall():
                    prev = scored.get(row['id'])
                    ts = float(row['score'] or 0.0)
                    if prev:
                        prev['score'] = min(1.0, prev['score'] + ts)
                    else:
                        scored[row['id']] = {
                            'content': row['content'],
                            'score': ts,
                        }
        except Exception as exc:
            _logger.warning('BM25-sökning över chunkar misslyckades: %s', exc)

        if not scored:
            return []

        ranked = sorted(
            scored.values(), key=lambda r: r['score'], reverse=True)[:k]
        return [r['content'] for r in ranked if r['content']]

    def _get_embeddings(self):
        """Get a langchain Embeddings instance for FAISS.

        Ordningen är avsiktlig och speglar `ai.provider._embedding_provider()`
        — den enda sanningen om vilken provider som kan skapa vektorer:

          1. `ai.provider` (Bifrost m.fl.) via en brygga. Detta är den väg
             som FUNGERAR i drift: providern är konfigurerad, nyckeln finns
             och `/embeddings` svarar.
          2. HuggingFace `sentence-transformers` som lokal fallback — men
             paketet är INTE installerat på den här servern, så i praktiken
             ger den alltid None.

        FYND (2026-10-27): före den här ändringen fanns bara väg 2 plus en
        `quest_id`-bunden LLM-väg. En agent-bunden RAG (`agent_id`, utan
        quest) hade därför INGEN fungerande embeddings-backend: varje
        `create_vector` föll tillbaka på `memory_type='text'` och varje
        `faiss_search` returnerade 0 träffar — tyst, med bara en varning i
        loggen. RAG-poster kunde alltså stå som `faiss` med `chunk_count`
        satt men 0-byte-index, utan att någonstans synas som fel.
        """
        try:
            # 1. ai.provider — den konfigurerade vektorkällan (Bifrost).
            bridge = self._provider_embeddings()
            if bridge is not None:
                return bridge

            # 2. Lokal fallback (kräver sentence-transformers).
            from langchain_community.embeddings import HuggingFaceEmbeddings
            return HuggingFaceEmbeddings(
                model_name='sentence-transformers/all-MiniLM-L6-v2'
            )

        except ImportError:
            _logger.warning(
                'Ingen embeddings-backend: ai.provider saknar can_embed och '
                'sentence-transformers är inte installerat. RAG-index kan '
                'inte byggas eller läsas — minnet faller tillbaka på text.')
            return None
        except Exception as e:
            _logger.warning('Embedding init failed: %s', e)
            return None

    def _provider_embeddings(self):
        """Brygga: `ai.provider` -> langchain `Embeddings`.

        Returnerar None när ingen provider kan embedda, så att anroparen kan
        falla vidare i stället för att få en halvfungerande instans.

        `input_type` är asymmetriskt och måste vara rätt: dokument som ska
        hittas embeddas som 'search_document', sökfrågan som 'search_query'.
        Att skicka samma typ för båda ger sämre träffar utan att något fel
        syns.
        """
        try:
            from langchain_core.embeddings import Embeddings as LcEmbeddings
        except ImportError:
            return None

        provider = self.env['ai.provider'].sudo()._embedding_provider()
        if not provider:
            return None

        class _ProviderEmbeddings(LcEmbeddings):
            def __init__(self, prov):
                self._prov = prov

            def embed_documents(self, texts):
                return self._prov._get_embedding_batch(
                    inputs=list(texts), input_type='search_document')

            def embed_query(self, text):
                return self._prov._get_embedding(
                    input=text, input_type='search_query')

        _logger.info(
            'RAG-embeddings via provider %s (%s)',
            provider.name, provider.embedding_model or 'default')
        return _ProviderEmbeddings(provider)

    # ════════════════════════════════════════════
    # OKF trigger-modell (task 5.1)
    # ════════════════════════════════════════════
    # FYND (2026-09-23): write()-hooken som satte `okf_dirty` togs bort.
    #
    # `ai.memory` pensionerades som OKF-konsument (okf-mixin F2, migration
    # 1.250): fälten okf_dirty/okf_indexed_at/artifact_type_id flyttade till
    # `ai.okf.mixin`, och `ai.memory` ärver inte längre den. Hooken skrev
    # ändå `vals['okf_dirty'] = True` → ValueError: Invalid field 'okf_dirty'
    # on model 'ai.memory' vid varje write (fångat i test_init_types_overhaul
    # och i ren nyinstallation).
    #
    # Historiken bakom flaggan (38 versioner av ai.memory,257, 2026-09-21) är
    # överspelad: ai.memory indexeras inte längre till OKF.

    # FYND (2026-09-29, okf-mixin F2.2/F2.4): fyra döda cron-metoder togs bort
    # HÄRIFRÅN — `_okf_cron_index_dirty`, `_okf_cron_index_dirty_legacy`,
    # `_okf_cron_index_dirty_memories` och
    # `_okf_cron_index_dirty_memories_legacy` — plus dubbletten
    # `_okf_cron_backfill_embeddings` (den kanoniska ligger på
    # `ai.okf.concept`, rad ~1947, och det är den cronen anropar).
    #
    # Den enda indexeringsvägen är `ai.okf.mixin._okf_cron_index_dirty()`.
    # Den itererar `_okf_indexable_models()`, som redan täcker
    # `ai.personal.memory` och `ai.company.memory`. Kvar här hade de bara
    # varit en tredje, avvikande kodväg — exakt det `test_okf_mixin.py:
    # test_mixin_has_no_own_indexing_path` förbjuder.
    #
    # `grep okf_dirty models/ai_memory.py` ska nu ge NOLL träffar.


