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

        OpenAI text-embedding-3-small (1536 dimensioner).
        Lagrar som PostgreSQL vector-literal: "[0.1,0.2,...]".

        Returns:
            str: PostgreSQL vector literal eller None
        """
        try:
            Provider = self.env['ai.provider']
            if Provider and hasattr(Provider, '_get_embedding'):
                embedding = Provider._get_embedding(
                    model='text-embedding-3-small',
                    input=text[:8192],
                )
                if embedding and isinstance(embedding, (list, tuple)):
                    return '[' + ','.join(str(v) for v in embedding) + ']'
        except Exception as e:
            _logger.debug('Provider embedding failed: %s', e)

        try:
            import requests
            provider = self.env['ai.provider'].search([
                ('active', '=', True),
            ], limit=1)
            if provider:
                url = provider.api_url or 'https://api.openai.com/v1/embeddings'
                api_key = provider.api_key
                resp = requests.post(
                    url,
                    headers={
                        'Authorization': f'Bearer {api_key}',
                        'Content-Type': 'application/json',
                    },
                    json={
                        'model': 'text-embedding-3-small',
                        'input': text[:8192],
                    },
                    timeout=10,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    embedding = data['data'][0]['embedding']
                    return '[' + ','.join(str(v) for v in embedding) + ']'
        except Exception as e:
            _logger.warning('Direct embedding failed: %s', e)

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
        try:
            Provider = self.env['ai.provider']
            if Provider and hasattr(Provider, '_get_embedding_batch'):
                embeddings = Provider._get_embedding_batch(
                    model='text-embedding-3-small',
                    input=truncated,
                )
                if embeddings and isinstance(embeddings, (list, tuple)):
                    return [
                        '[' + ','.join(str(v) for v in emb) + ']'
                        for emb in embeddings
                    ]
        except Exception:
            pass
        return [self._generate_embedding(t) for t in truncated]

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
