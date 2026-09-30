# -*- coding: utf-8 -*-
"""ai.memory — FAISS/pgvector memory for agents."""

import logging
from odoo import models, fields, api

from .ai_okf_concept import EMBEDDING_DIM as _OKF_EMBEDDING_DIM

_logger = logging.getLogger(__name__)


class AIMemory(models.Model):
    _name = 'ai.memory'

    # OKF-taggar: egen relationstabell (en many2many kan inte ligga
    # pa en abstrakt mixin — den ger samma tabell for alla arvande).
    okf_tags = fields.Many2many(
        'ai.okf.tag', 'ai_memory_okf_tag_rel', 'res_id', 'tag_id',
        string='OKF Tags')
    _description = 'AI Memory'
    _order = 'create_date desc'

    name = fields.Char('Memory Key')
    content = fields.Text('Content')
    memory_type = fields.Selection([
        ('faiss', 'FAISS Vector'),
        ('pgvector', 'pgvector'),
        ('text', 'Plain Text'),
    ], default='text')

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

    # ── FAISS via ir.attachment ──
    faiss_attachment_id = fields.Many2one('ir.attachment',
        string='FAISS Index',
        help='Serialized FAISS vector index stored as attachment')
    source_attachment_id = fields.Many2one('ir.attachment',
        string='Source File',
        help='The original uploaded file (ir.attachment) this memory '
             'was created from. Used by the chat UI to render a '
             'download link.')
    chunk_count = fields.Integer('Chunk Count', default=0,
        help='Number of document chunks in the FAISS index')

    # ── FAISS vector operations ──

    def create_vector(self, documents: list, embedding_model_name: str = ''):
        """Split documents, embed, and store as FAISS via ir.attachment.

        Args:
            documents: List of langchain Document objects
            embedding_model_name: Name of the embedding model used

        Returns:
            Number of chunks created
        """
        self.ensure_one()
        if not documents:
            return 0

        try:
            from langchain_text_splitters import RecursiveCharacterTextSplitter
            from langchain_community.vectorstores import FAISS
            import base64 as b64

            # Split documents
            splitter = RecursiveCharacterTextSplitter(
                chunk_size=1000,
                chunk_overlap=200,
                add_start_index=True,
            )
            chunks = splitter.split_documents(documents)
            if not chunks:
                return 0

            # Get embeddings from the quest's agent LLM
            # Fall back to Bifrost for embeddings
            embeddings = self._get_embeddings()
            if not embeddings:
                _logger.warning('No embedding model available, storing text-only')
                self.memory_type = 'text'
                self.content = '\n\n'.join(
                    d.page_content[:1000] for d in chunks[:50]
                )
                return len(chunks)

            # Build FAISS index
            db = FAISS.from_documents(chunks, embeddings)

            # Serialize and store as ir.attachment
            serialized = db.serialize_to_bytes()
            attachment = self.env['ir.attachment'].create({
                'name': f'faiss_{self.name}_{self.id}.bin',
                'datas': b64.b64encode(serialized),
                'res_model': 'ai.memory',
                'res_id': self.id,
                'mimetype': 'application/octet-stream',
            })

            self.write({
                'faiss_attachment_id': attachment.id,
                'chunk_count': len(chunks),
                'embedding_model': embedding_model_name or 'bifrost-default',
                'memory_type': 'faiss',
            })

            _logger.info('FAISS index created: %d chunks for memory %s',
                        len(chunks), self.name)
            return len(chunks)

        except ImportError as e:
            _logger.warning('FAISS dependencies missing: %s', e)
            self.memory_type = 'text'
            self.content = '\n\n'.join(
                d.page_content[:1000] for d in documents[:50]
            )
            return len(documents)
        except Exception as e:
            _logger.error('FAISS create_vector failed: %s', e)
            raise

    def load_faiss(self):
        """Load FAISS index from ir.attachment.

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

    def faiss_search(self, query: str, k: int = 3) -> list[str]:
        """Search FAISS index for similar documents.

        NOTE: heter faiss_search (inte search) — annars skuggar metoden
        ORM:ts sökmetod och alla env['ai.memory'].search([domain])-anrop
        träffar fel (ai-memory-shadowing-fix).

        Args:
            query: Search query string
            k: Number of results to return

        Returns:
            List of document content strings
        """
        self.ensure_one()
        db = self.load_faiss()
        if not db:
            return []

        try:
            docs = db.similarity_search(query, k=k)
            results = []
            for doc in docs:
                if doc and doc.page_content:
                    results.append(doc.page_content)

            self.write({
                'last_accessed': fields.Datetime.now(),
                'access_count': self.access_count + 1,
            })
            return results

        except Exception as e:
            _logger.error('FAISS search failed for memory %s: %s', self.name, e)
            return []

    def _get_embeddings(self):
        """Get embeddings instance from configured LLM or Bifrost fallback."""
        try:
            # Try quest's agent LLM first
            if self.quest_id:
                for agent_rel in self.quest_id.agent_ids:
                    agent = agent_rel.agent_id
                    if agent and hasattr(agent, 'ai_agent_llm_id'):
                        llm = agent.ai_agent_llm_id
                        if llm and hasattr(llm, 'get_embedding'):
                            emb = llm.get_embedding()
                            if emb:
                                return emb

            # Fallback: basic HuggingFace embedding
            from langchain_community.embeddings import HuggingFaceEmbeddings
            return HuggingFaceEmbeddings(
                model_name='sentence-transformers/all-MiniLM-L6-v2'
            )

        except ImportError:
            _logger.warning('No embedding backend available')
            return None
        except Exception as e:
            _logger.warning('Embedding init failed: %s', e)
            return None

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


