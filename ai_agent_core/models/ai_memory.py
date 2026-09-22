# -*- coding: utf-8 -*-
"""ai.memory — FAISS/pgvector memory for agents."""

import logging
from odoo import models, fields, api

from .ai_okf_concept import EMBEDDING_DIM as _OKF_EMBEDDING_DIM

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
        'OKF Dirty', default=False, index=True, copy=False,
        help='Sätts av write()-hook; lätt cron (5 min) indexerar och rensar.')
    # FYND (2026-09-21): `_okf_cron_index_dirty_memories` skrev
    # `okf_indexed_at` — men fältet fanns bara på `ai.memory.mixin`, som
    # `ai.memory` INTE ärver. Skrivningen hade kraschat i det ögonblick
    # den nåddes (och gjorde det tyst: felet fångades av cronens
    # try/except och loggades som en varning). Fältet deklareras därför
    # här, med samma innebörd som på mixin.
    okf_indexed_at = fields.Datetime(
        'OKF Indexed At', readonly=True, copy=False,
        help='När posten senast indexerades till OKF.')

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
    def write(self, vals):
        """write()-hook: sätt okf_dirty utan AI-arbete.

        FYND (2026-09-21): hooken satte flaggan VILLKORSLÖST, även när
        anroparen uttryckligen ville RENSA den (`okf_dirty=False`). Cronen
        rensar med `mem.write({'okf_dirty': False})` — vilket gick rakt in i
        denna hook och satte flaggan igen. Varje cron-varv (5 min) skapade
        därför en ny OKF-version: `ai.memory,257` hade 38 versioner med
        identiskt innehåll ("hello test"), 11:36 → 15:06.

        Fixen: en explicit rensning (`False`) respekteras — den är ett
        medvetet beslut av cronen efter lyckad indexering, inte en
        innehållsändring. Flaggan sätts bara när anroparen inte sagt något
        om den alls, eller uttryckligen satt den till True.

        `ai.memory.mixin._set_okf_dirty()` löser samma sak genom att sätta
        flaggan direkt i SQL; den vägen är kvar för mixin-modellerna. Här
        räcker det att hooken slutar motarbeta sin egen anropare.
        """
        if 'okf_dirty' not in vals and not vals.get('consolidated'):
            vals['okf_dirty'] = True
        return super().write(vals)

    @api.model
    def _okf_cron_index_dirty(self, batch_size=50):
        """Lätt cron (task 5.2): plocka upp dirty-artefakter, indexera,
        rensa dirty-flag. Tungt arbete görs HÄR, inte i write().

        Fas 6 (D7): bryggan täcker nu TRE modeller — `ai.memory` (som redan
        hade flaggan) samt `ai.personal.memory` och `ai.company.memory`
        (legacy-minnena, den enda plats där svensk BM25 någonsin fungerade).
        Utan dem är migreringen halvfärdig: skrivsidan flyttad, läsningen
        kvar i en stack som töms.
        """
        total = self._okf_cron_index_dirty_memories(batch_size)
        total += self._okf_cron_index_dirty_legacy(
            'ai.personal.memory', batch_size)
        total += self._okf_cron_index_dirty_legacy(
            'ai.company.memory', batch_size)
        return total

    @api.model
    def _okf_cron_index_dirty_legacy(self, model_name, batch_size=50):
        """Indexera dirty-poster från ett legacy-minnesmodell (fas 6.3).

        Idempotent: flaggan rensas bara när `_okf_upsert` returnerat ett
        koncept. Misslyckas indexeringen ligger posten kvar och görs om.
        """
        if model_name not in self.env:
            return 0
        Model = self.env[model_name]
        dirty = Model.sudo().search(
            [('okf_dirty', '=', True)], limit=batch_size,
            order='write_date asc')
        if not dirty:
            return 0
        count = 0
        for mem in dirty:
            try:
                vals = mem._okf_concept_vals()
                if not vals.get('summary'):
                    # Tom post — inget att indexera. Rensa flaggan så att
                    # den inte blockerar kön för evigt.
                    #
                    # `_set_okf_dirty`-vägen finns inte på legacy-modellerna
                    # (den ligger på mixin, men sätter bara TRUE). En tom
                    # post ska inte indexeras — därför skrivs flaggan med
                    # `sudo()` och en explicit False, vilket mixinens
                    # write()-hook respekterar eftersom den bara tittar på
                    # `dirty_fields` (content, archived, …) och
                    # `okf_dirty` inte ingår där.
                    mem.sudo().write({'okf_dirty': False})
                    continue
                concept = self.env['ai.okf.concept']._okf_upsert(
                    artifact_type='learning', **vals)
                if concept:
                    mem.sudo().write({
                        'okf_dirty': False,
                        'okf_indexed_at': fields.Datetime.now(),
                    })
                    count += 1
            except Exception as e:
                _logger.warning(
                    'OKF cron index failed for %s %s: %s',
                    model_name, mem.id, e)
        return count

    @api.model
    def _okf_cron_index_dirty_memories(self, batch_size=50):
        """Indexera dirty-poster från `ai.memory` (ursprunglig brygga)."""
        # ai.memory har en FAISS-hjälpmetod som skuggar ORM:ts search —
        # använd _search för att komma åt ORM:en
        dirty_ids = self._search([('okf_dirty', '=', True)], limit=batch_size)
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
                    # Skriv BÅDE flaggan och tidsstämpeln. Med den
                    # fixade write()-hooken (explicit False respekteras)
                    # stannar flaggan rensad — tidigare tände hooken den
                    # igen och samma post indexerades om var 5:e minut.
                    mem.write({
                        'okf_dirty': False,
                        'okf_indexed_at': fields.Datetime.now(),
                    })
                    count += 1
            except Exception as e:
                _logger.warning('OKF cron index failed for memory %s: %s',
                                mem.id, e)
        return count

    @api.model
    def _okf_cron_backfill_embeddings(self, batch_size=20):
        """Efterfyllnad av saknade vektorer (okf-recall-path fas 3.3).

        Plockar koncept vars `embedding_state` inte är 'ready' och försöker
        skapa vektorn. Idempotent: lyckade rader markeras 'ready' och plockas
        aldrig upp igen; misslyckade lämnas i sin markering.

        VARFÖR EN EGEN CRON: koncept skrivna innan embeddings fungerade har
        en tom vektorkolumn. Utan efterfyllnad kräver varje sådan rad en
        manuell åtgärd — och utan `embedding_state` går det inte att skilja
        "aldrig försökt" från "försökt och misslyckats".

        Avsiktligt utan tung logik: tunga saker händer i `_produce_embedding`
        som REDAN körs via `_okf_upsert` på nya koncept. Denna cron räddar
        bara eftersläntrare.
        """
        Concept = self.env['ai.okf.concept']
        pending = Concept.search([
            ('embedding_state', 'in', ('pending', 'failed')),
            ('archived', '=', False),
            ('status', '!=', 'superseded'),
        ], limit=batch_size, order='id asc')

        if not pending:
            return 0

        provider = self.env['ai.provider']._embedding_provider()
        if not provider:
            _logger.warning(
                'OKF efterfyllnad: ingen provider som kan embedda — %s koncept '
                'väntar fortfarande', len(pending))
            return 0

        # Skicka INGET model-argument. `_effective_embedding_model` har
        # prioritet argument → fält → konstant, och konstanten
        # (DEFAULT_EMBEDDING_MODEL = 'text-embedding-3-small') är den modell
        # Bifrost inte svarar på (tyst timeout 20 s × 3 försök). Genom att
        # skicka in den som argument kringgicks provider-fältets värde
        # (`mistral/mistral-embed`, svarar på 0,2 s) och efterfyllnaden
        # fastnade i en timeout-loop — 450 koncept låg kvar som 'pending'
        # medan cronen brann sin tid.
        #
        # Samma fälla som ai_memory_mixin._generate_embedding redan
        # dokumenterar. Denna väg hade den kvar.
        model = provider._effective_embedding_model()
        filled = 0
        for concept in pending:
            text = ' '.join(filter(None, [concept.title, concept.summary])).strip()
            if not text:
                # 'skipped' får skrivas — det är ett livscykelfält. Ingen ny
                # version: det finns inget innehåll att versionera, och en
                # tom kopia vore bara skräp i versionskedjan.
                concept.write({'embedding_state': 'skipped'})
                continue

            vector = provider._get_embedding(
                input=text, input_type='search_document')
            if not vector:
                # Lämna som pending — nästa körning försöker igen.
                # Vi kan inte märka om raden utan att skapa en ny version,
                # så vi rör den inte alls: 'pending' är redan sanningen.
                continue

            if not provider._validate_embedding(vector, model=model,
                                                dim=_OKF_EMBEDDING_DIM):
                # Fel dimension: markera 'failed' så den kräver tillsyn.
                concept.write({'embedding_state': 'failed'})
                continue

            # VIKTIGT: koncept-rader är ADD-only (beslut 10). Vektorn kan
            # alltså inte skrivas in i den befintliga raden — efterfyllnaden
            # skapar en NY VERSION via _okf_upsert. Den gamla raden blir
            # 'superseded' och den nya bär vektorn. Immutabiliteten är
            # bevarad: historiken finns kvar, inget skrivs över.
            owner = self._okf_owner_for_concept(concept)
            self.env['ai.okf.concept']._okf_upsert(
                artifact_type=concept.artifact_type_id or 'learning',
                concept_key=concept.concept_key,
                summary=concept.summary,
                title=concept.title,
                source_ref=concept.source_ref,
                entities=concept.entities,
                generated_by='backfill',
                embedding=vector,
                **owner
            )
            filled += 1

        _logger.info('OKF efterfyllnad: %s av %s koncept fick vektor',
                     filled, len(pending))
        return filled

    @api.model
    def _okf_owner_for_concept(self, concept):
        """Plocka ut ägar-argumenten från ett koncept för _okf_upsert.

        _okf_upsert kräver exakt ett ägarfält — inte ett browse-id.
        """
        if concept.owner_company_id:
            return {'owner_company_id': concept.owner_company_id.id}
        if concept.owner_user_id:
            return {'owner_user_id': concept.owner_user_id.id}
        if concept.owner_coworker_id:
            return {'owner_coworker_id': concept.owner_coworker_id.id}
        return {'owner_company_id': self.env.company.id}
