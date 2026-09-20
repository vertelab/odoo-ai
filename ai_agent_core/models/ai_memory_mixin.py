# -*- coding: utf-8 -*-
"""ai.memory.mixin — Abstract mixin för hybrid memory search (pgvector+tsvector+entity).

Delad av ai.personal.memory och ai.company.memory.
Innehåller all gemensam logik för:
- ADD-only
- Hybrid search (pgvector + tsvector + entity boost)
- Embedding via AI-provider
- Entity extraction
- BM25-normalisering
"""

import json
import logging
import math
import re
from datetime import date, datetime, timedelta

from odoo import models, fields, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class AIMemoryMixin(models.AbstractModel):
    _name = 'ai.memory.mixin'
    _description = 'Memory Mixin — hybrid search for personal and company memory'
    _auto = False  # Abstract model, no DB table

    # ════════════════════════════════════════════
    # OKF-BRYGGAN (D7, fas 6)
    # ════════════════════════════════════════════
    # Legacy-minnena är den enda plats där svensk BM25 någonsin fungerade.
    # När de skrivs ska de därför också bli OKF-koncept — annars är
    # migreringen halvfärdig: skrivsidan flyttad, läsningen kvar i en
    # stack som töms.
    #
    # Flaggan sätts i write()/create() och konsumeras av
    # `ai.memory._okf_cron_index_dirty()`. Indexeringen sker i cron — inte
    # i write() — så att en användares skrivning aldrig väntar på en
    # LLM/HTTP-tur.
    okf_dirty = fields.Boolean(
        'OKF Dirty', default=True, index=True, copy=False,
        help='Satt när posten behöver indexeras om till ett OKF-koncept. '
             'Rensas av cronen när indexeringen lyckats.')
    okf_indexed_at = fields.Datetime(
        'OKF Indexed At', readonly=True, copy=False,
        help='När posten senast indexerades till OKF.')

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        # Nya poster ska indexeras.
        records.filtered(lambda r: not r.okf_dirty).write({'okf_dirty': True})
        return records

    def write(self, vals):
        # FYND (fas 6): bägge legacy-modellerna är ADD-ONLY — `content` kan
        # inte ändras efter skapande (`_check_add_only` kastar UserError).
        # En hook på innehållsändring är därför till stor del teoretisk: den
        # enda vägen till ett nytt innehåll är en NY post.
        #
        # Kroken finns ändå kvar, av två skäl:
        #   1. Fälten som INTE är innehåll (archived, importance, entities)
        #      går att ändra, och en arkivering ska slå igenom på konceptet.
        #   2. Om ADD-only någon gång luckras upp är bryggan redan hel —
        #      det är billigare än att upptäcka det i efterhand.
        dirty_fields = {'content', 'content_preview', 'category',
                        'importance', 'entities', 'archived', 'scope'}
        result = super().write(vals)
        if dirty_fields & set(vals):
            # Undvik oändlig rekursion: skriv inte flaggan via write()
            # när vi redan står i write().
            self.sudo()._set_okf_dirty()
        return result

    def _set_okf_dirty(self):
        """Sätt okf_dirty direkt i SQL — kringgår write()-hooken.

        `flush_recordset()` först: annars kan ORM:ens ännu icke-skrivna
        buffert skrivas EFTER vårt `UPDATE` och skriva över flaggan med det
        gamla värdet. Det var precis vad som hände i testet — flaggan sattes
        och försvann i samma andetag.
        """
        if not self:
            return
        self.flush_recordset(['okf_dirty'])
        self.env.cr.execute(
            'UPDATE %s SET okf_dirty = TRUE WHERE id = ANY(%%s)' % self._table,
            (list(self.ids),))
        self.invalidate_recordset(['okf_dirty'])

    def _okf_owner_vals(self):
        """Härled OKF-ägaren ur en legacy-minnespost (fas 6.3)."""
        self.ensure_one()
        if self._name == 'ai.personal.memory':
            return {'owner_user_id': self.user_id.id or None}
        return {'owner_company_id': self.company_id.id or self.env.company.id}

    def _okf_concept_vals(self):
        """Bygg `_okf_upsert`-argumenten ur en legacy-post (fas 6.3).

        Nyckeln måste vara STABIL över tid: samma minnespost ska alltid
        mappa till samma OKF-koncept, annars skapas en ny version vid varje
        indexering och versionskedjan svämmar över.
        """
        self.ensure_one()
        vals = {
            'concept_key': '%s,%s' % (self._name, self.id),
            'summary': (self.content or '')[:2000],
            'title': (self.content_preview or '')[:120] or None,
            'source_ref': '%s,%s' % (self._name, self.id),
            'generated_by': 'cron',
        }
        vals.update(self._okf_owner_vals())
        return vals

    # ════════════════════════════════════════════
    # HYBRID SEARCH — tre signaler
    # ════════════════════════════════════════════

    @api.model
    def _search_memory(self, domain, query=None, limit=10, threshold=0.1,
                       include_archived=False, explain=False, order='score'):
        """Hybrid search — pgvector + tsvector + entity boost.

        Använder mem0s multi-signal retrieval:
        1. Semantic (pgvector cosine similarity)
        2. BM25 (tsvector full-text, svensk)
        3. Entity boost (extraherade entiteter)

        Args:
            domain (list): Odoo domain for base filtering (scope)
            query (str, optional): Sökfråga. None = returnera senaste.
            limit (int): Max resultat
            threshold (float): Minimum semantic score
            include_archived (bool): Inkludera arkiverade
            explain (bool): Inkludera score_details
            order (str): 'score' (hybrid) | 'create_date'

        Returns:
            list[dict]: Sorterade resultat
        """
        table = self._table
        if not include_archived:
            domain.append(('archived', '=', False))

        # Utan query: returnera senaste
        if not query or not query.strip():
            records = self.search(domain, limit=limit, order='create_date desc')
            return [{
                'id': r.id,
                'content': r.content,
                'category': r.category,
                'importance': r.importance,
                'create_date': r.create_date,
                'score': 1.0,
            } for r in records]

        # ════════════════════════════════════════
        # SIGNAL 1: Semantic search (pgvector)
        # ════════════════════════════════════════
        semantic_results = []
        query_embedding = None
        try:
            query_embedding = self._generate_embedding(query)
        except Exception as e:
            _logger.warning('Query embedding failed: %s', e)

        if query_embedding:
            # Build WHERE clause from domain
            where_clauses = ['archived = %s']
            params = [include_archived]
            for clause in domain:
                if isinstance(clause, (list, tuple)) and len(clause) == 3:
                    field, op, val = clause
                    if field in ('archived',):
                        continue
                    if op == '=':
                        where_clauses.append(f'{field} = %s')
                        params.append(val)
                    elif op == 'in':
                        placeholders = ','.join(['%s'] * len(val))
                        where_clauses.append(f'{field} IN ({placeholders})')
                        params.extend(val)

            where_sql = ' AND '.join(where_clauses)

            self.env.cr.execute(f"""
                SELECT id, content, category, importance,
                       create_date,
                       1 - (embedding <=> %s::vector) AS semantic_score
                FROM {table}
                WHERE {where_sql}
                  AND embedding IS NOT NULL
                  AND 1 - (embedding <=> %s::vector) >= %s
                ORDER BY semantic_score DESC
                LIMIT %s
            """, (query_embedding, query_embedding, threshold, limit * 4) + tuple(params))
            semantic_results = self.env.cr.dictfetchall()

        # ════════════════════════════════════════
        # SIGNAL 2: BM25 (tsvector full-text)
        # ════════════════════════════════════════
        bm25_scores = {}
        try:
            self.env.cr.execute(f"""
                SELECT id,
                       ts_rank(search_vector,
                               plainto_tsquery('swedish', %s)) AS bm25_score
                FROM {table}
                WHERE {' AND '.join(where_clauses)}
                  AND search_vector @@ plainto_tsquery('swedish', %s)
                ORDER BY bm25_score DESC
                LIMIT %s
            """, (query, query, limit * 4) + tuple(params))
            bm25_results = self.env.cr.dictfetchall()
            bm25_scores = {
                r['id']: self._normalize_bm25(r['bm25_score'])
                for r in bm25_results if r['bm25_score']
            }
        except Exception as e:
            _logger.warning('BM25 search failed: %s', e)

        # ════════════════════════════════════════
        # SIGNAL 3: Entity boost
        # ════════════════════════════════════════
        query_entities = self._extract_entities(query)
        entity_boosts = self._compute_entity_boosts(query_entities, domain)

        # ════════════════════════════════════════
        # FUSION: score_and_rank
        # ════════════════════════════════════════
        has_bm25 = bool(bm25_scores)
        has_entity = bool(entity_boosts)
        max_possible = 1.0
        if has_bm25:
            max_possible += 1.0
        if has_entity:
            max_possible += 0.5

        seen_ids = set()
        scored = []

        for r in semantic_results:
            mem_id = r['id']
            seen_ids.add(mem_id)
            semantic = r['semantic_score']
            bm25 = bm25_scores.get(mem_id, 0.0)
            entity = entity_boosts.get(mem_id, 0.0)
            combined = min((semantic + bm25 + entity) / max_possible, 1.0)

            result = {
                'id': mem_id,
                'content': r['content'],
                'category': r['category'],
                'importance': r['importance'],
                'create_date': r['create_date'],
                'score': combined,
            }
            if explain:
                result['score_details'] = {
                    'semantic': semantic,
                    'bm25': bm25,
                    'entity_boost': entity,
                }
            scored.append(result)

        # Lägg till BM25/entity-träffar som inte fanns i semantic
        all_bm25_ids = set(bm25_scores.keys())
        for mem_id in (all_bm25_ids - seen_ids):
            bm25 = bm25_scores.get(mem_id, 0.0)
            entity = entity_boosts.get(mem_id, 0.0)
            combined = min((0.0 + bm25 + entity) / max_possible, 1.0)

            record = self.browse(mem_id)
            if record.exists():
                scored.append({
                    'id': mem_id,
                    'content': record.content,
                    'category': record.category,
                    'importance': record.importance,
                    'create_date': record.create_date,
                    'score': combined,
                    'score_details': {
                        'semantic': 0.0, 'bm25': bm25, 'entity_boost': entity,
                    } if explain else None,
                })
            seen_ids.add(mem_id)

        scored.sort(key=lambda x: x['score'], reverse=True)
        top = scored[:limit]

        # Uppdatera access_count
        top_ids = [r['id'] for r in top if r.get('id')]
        if top_ids:
            self.browse(top_ids).write({
                'last_accessed': fields.Datetime.now(),
            })

        return top

    # ════════════════════════════════════════════
    # EMBEDDING
    # ════════════════════════════════════════════

    @api.model
    def _generate_embedding(self, text):
        """Generera embedding via AI-provider.

        OpenAI text-embedding-3-small (1024 dimensioner — kolumnens dimension).
        Lagrar som PostgreSQL vector-literal: "[0.1,0.2,...]".

        Returns:
            str: PostgreSQL vector literal eller None
        """
        # `_get_embedding` kräver en SINGEL provider (`ensure_one()`), så den
        # får inte anropas på ett tomt recordset — det ger
        # "Expected singleton: ai.provider()" och minnet tappar sin vektor.
        # `_embedding_provider()` är den avsedda uppslagningen: en aktiv
        # provider med can_embed, annars en bifrost-provider med nyckel.
        provider = self.env['ai.provider'].sudo()._embedding_provider()
        if not provider:
            _logger.warning(
                'Embedding: ingen provider kan skapa vektorer '
                '(can_embed saknas) — minnet sparas utan vektor')
            return None
        # Skicka INGET model-argument. `_effective_embedding_model` har
        # prioritet argument → fält → konstant, och konstanten
        # (DEFAULT_EMBEDDING_MODEL = 'text-embedding-3-small') är den modell
        # Bifrost AVVISAR (401). Genom att skicka in den som argument
        # kringgick vi fältets värde och tvingade fram det trasiga valet.
        embedding = provider._get_embedding(input=text[:8192])
        if embedding and isinstance(embedding, (list, tuple)):
            return '[' + ','.join(str(v) for v in embedding) + ']'
        return None

    @api.model
    def embed_batch(self, texts):
        """Batch-embeddning.

        Args:
            texts (list[str]): Texter att embedda
        Returns:
            list[str|None]: PostgreSQL vector-literals
        """
        if not texts:
            return []
        truncated = [t[:8192] for t in texts]
        Provider = self.env['ai.provider']
        # Providern, modellen och dimensionen kommer från providerns egna
        # fält — inte från en hårdkodad sträng. 'text-embedding-3-small'
        # stod här och pekade på en modell Bifrost avvisar (000/401).
        emb_provider = Provider._embedding_provider()
        if not emb_provider:
            _logger.warning(
                'Embedding (batch): ingen provider som kan embedda — '
                '%s texter lämnas utan vektor', len(truncated))
            return [None] * len(truncated)
        embeddings = emb_provider._get_embedding_batch(
            inputs=truncated,
            input_type='search_document',
        )
        if embeddings and isinstance(embeddings, (list, tuple)):
            return [
                '[' + ','.join(str(v) for v in emb) + ']'
                if emb else None
                for emb in embeddings
            ]
        return [None] * len(truncated)

    # ════════════════════════════════════════════
    # ENTITY EXTRACTION
    # ════════════════════════════════════════════

    @api.model
    def _extract_entities(self, text):
        """Extrahera entities från text (regex-baserad MVP).

        Returns:
            list[dict]: Entities med type och text
        """
        if not text:
            return []
        entities = []
        quoted = re.findall(r'"([^"]+)"', text)
        for q in quoted[:5]:
            entities.append({'type': 'QUOTED', 'text': q.strip()[:50]})
        proper = re.findall(r'\b([A-ZÅÄÖ][A-ZÅÄÖ0-9]{1,5})\b', text)
        for p in proper[:5]:
            entities.append({'type': 'PROPER', 'text': p})
        codes = re.findall(r'\b(\d{4,6})\b', text)
        for c in codes[:3]:
            entities.append({'type': 'CODE', 'text': c})
        finance_terms = [
            'periodiseringsfond', 'avskrivning', 'moms', 'bokslut',
            'resultaträkning', 'balansräkning', 'skatteverket',
            'f-skatt', 'egenavgift', 'K2', 'K3', 'BAS',
        ]
        for term in finance_terms:
            if term.lower() in text.lower():
                entities.append({'type': 'TOPIC', 'text': term})
        return entities

    # ════════════════════════════════════════════
    # BM25 NORMALISERING
    # ════════════════════════════════════════════

    @staticmethod
    def _normalize_bm25(raw_score):
        """Normalisera BM25-score till [0, 1] med sigmoid (mem0-mönster)."""
        if not raw_score or raw_score <= 0:
            return 0.0
        midpoint = 7.0
        steepness = 0.6
        return 1.0 / (1.0 + math.exp(-steepness * (raw_score - midpoint)))

    # ════════════════════════════════════════════
    # ENTITY BOOST
    # ════════════════════════════════════════════

    @api.model
    def _compute_entity_boosts(self, query_entities, domain):
        """Beräkna entity boost per minne."""
        if not query_entities:
            return {}
        boosts = {}
        table = self._table
        entity_texts = [e['text'] for e in query_entities[:8]]

        for entity_text in entity_texts:
            if not entity_text:
                continue
            try:
                self.env.cr.execute(f"""
                    SELECT id FROM {table}
                    WHERE archived = FALSE
                      AND entities IS NOT NULL
                      AND entities::text ILIKE %s
                    LIMIT 50
                """, (f'%{entity_text}%',))
                for row in self.env.cr.dictfetchall():
                    mem_id = row['id']
                    boosts[mem_id] = min(boosts.get(mem_id, 0) + 0.25, 0.5)
            except Exception:
                pass
        return boosts

    # ════════════════════════════════════════════
    # SYSTEM PROMPT INJECTION (Hermes-mönster)
    # ════════════════════════════════════════════

    @api.model
    def _build_memory_block(self, memories, max_chars, header_label):
        """Bygg Hermes-kompatibel system prompt block.

        Args:
            memories (list[dict]): Minnes-resultat
            max_chars (int): Max tecken
            header_label (str): Etikett för headern (e.g. "USER PROFILE")

        Returns:
            str: Formatterad markdown-block
        """
        if not memories:
            return ''

        entries = []
        chars = 0
        for mem in memories:
            entry = mem['content']
            if chars + len(entry) > max_chars:
                break
            entries.append(entry)
            chars += len(entry)

        content = '\n§ '.join(entries)
        pct = min(100, int(chars / max_chars * 100)) if max_chars else 0

        header = f"{header_label} [{pct}% — {chars:,}/{max_chars:,} chars]"
        separator = '═' * 46

        return f"{separator}\n{header}\n{separator}\n{content}"

    # ════════════════════════════════════════════
    # HTML → TEXT
    # ════════════════════════════════════════════

    @staticmethod
    def _html_to_text(html):
        if not html:
            return ''
        try:
            from html.parser import HTMLParser
            class MLStripper(HTMLParser):
                def __init__(self):
                    super().__init__()
                    self.reset()
                    self.text = []
                def handle_data(self, d):
                    self.text.append(d)
            s = MLStripper()
            s.feed(html)
            return ''.join(s.text).strip()
        except Exception:
            return re.sub(r'<[^>]+>', '', html).strip()
