# -*- coding: utf-8 -*-
"""ai.memory — FAISS/pgvector memory for agents."""

import logging
from odoo import models, fields, api

_logger = logging.getLogger(__name__)


class AIMemory(models.Model):
    _name = 'ai.memory'
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

    # OKF artifact type (registrerbar taxonomi, ersätter statiska selections)
    artifact_type_id = fields.Many2one(
        'ai.artifact.type', string='Artifact Type',
        help='OKF artifact type (learning = memory kind, övriga = knowledge).'
             ' Befintliga poster får default learning via data/init.')

    # OKF dirty-flag (trigger-modell, task 5.1) — sätts av write()-hooken
    # (microseconds, inget AI-arbete); lätt cron plockar upp och rensar.
    okf_dirty = fields.Boolean(
        'OKF Dirty', default=False,
        help='Sätts av write()-hook; lätt cron (5 min) indexerar och rensar.')

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

            data = b64.b64decode(self.faiss_attachment_id.datas)
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

    def search(self, query: str, k: int = 3) -> list[str]:
        """Search FAISS index for similar documents.

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
            if self.coworker_id:
                for agent_rel in self.coworker_id.agent_ids:
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
    def write(self, vals):
        """write()-hook: sätt okf_dirty utan AI-arbete."""
        if vals.get('okf_dirty') is not True and not vals.get('consolidated'):
            vals['okf_dirty'] = True
        return super().write(vals)

    @api.model
    def _okf_cron_index_dirty(self):
        """Lätt cron (task 5.2): plocka upp dirty-artefakter, indexera,
        rensa dirty-flag. Tungt arbete görs HÄR, inte i write()."""
        # ai.memory har en FAISS-hjälpmetod som skuggar ORM:ts search —
        # använd _search för att komma åt ORM:en
        dirty_ids = self._search([('okf_dirty', '=', True)], limit=50)
        dirty = self.browse(dirty_ids)
        if not dirty:
            return 0
        count = 0
        for mem in dirty:
            try:
                atype = mem.artifact_type_id
                # Sammanfattning = innehållet (tunt koncept; vid behov kan
                # en AI-genererad summary läggas till här)
                summary = mem.content or mem.name or ''
                concept_key = 'ai.memory,%s' % mem.id
                owner_coworker_id = mem.quest_id.id or None
                owner_user_id = None
                owner_company_id = None
                if owner_coworker_id:
                    pass  # coworker-scope
                elif mem.identity_id:
                    owner_user_id = mem.identity_id.user_id.id or None
                # Företag om ingen ägare hittas
                if not owner_coworker_id and not owner_user_id:
                    owner_company_id = self.env.company.id

                concept = self.env['ai.okf.concept']._okf_upsert(
                    artifact_type=atype or 'learning',
                    concept_key=concept_key,
                    summary=summary,
                    title=mem.name,
                    source_ref=concept_key,
                    owner_company_id=owner_company_id,
                    owner_user_id=owner_user_id,
                    owner_coworker_id=owner_coworker_id,
                    generated_by='cron',
                )
                if concept:
                    mem.write({'okf_dirty': False})
                    count += 1
            except Exception as e:
                _logger.warning('OKF cron index failed for memory %s: %s',
                                mem.id, e)
        return count
