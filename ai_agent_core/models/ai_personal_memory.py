# -*- coding: utf-8 -*-
"""ai.personal.memory — Personligt minne som följer användaren över alla quests.

Design inspirerad av:
- mem0: ADD-only, multi-signal retrieval (pgvector + BM25 + entity boost)
- Hermes Agent: USER.md frozen snapshot i system prompt
- odoomind: cross-graph mail ↔ Odoo-data via res.partner.email

Minnet följer PERSONEN (res.users), inte en specifik ai.coworker.
Alla quests som användaren interagerar med kan använda samma minne.
"""

import json
import logging
import math
import re
from datetime import date, datetime, timedelta

from odoo import models, fields, api, _
from odoo.exceptions import UserError, AccessError

_logger = logging.getLogger(__name__)


class AIPersonalMemory(models.Model):
    _name = 'ai.personal.memory'
    _description = 'Personal Memory — follows the user across all quests'
    _order = 'create_date desc'
    _rec_name = 'content_preview'
    _inherit = 'ai.memory.mixin'

    # ════════════════════════════════════════════
    # SCOPE — MINNET FÖLJER PERSONEN
    # ════════════════════════════════════════════
    user_id = fields.Many2one(
        'res.users', string='User', required=True, index=True,
        default=lambda self: self.env.user,
        help='The user this memory belongs to. '
             'Accessible from ANY quest the user interacts with.')
    company_id = fields.Many2one(
        'res.company', string='Company', index=True,
        default=lambda self: self.env.company,
        help='Company-level memory (policies, goals).')

    # Source tracking (VEM skapade minnet)
    source_coworker_id = fields.Many2one(
        'ai.coworker', string='Source Quest', index=True,
        help='Which quest created this memory.')
    source_session_id = fields.Many2one(
        'ai.coworker.session', string='Source Session', index=True,
        help='Which session created this memory.')
    source = fields.Selection([
        ('chat', 'Chat Conversation'),
        ('mail', 'Email'),
        ('mail_graph', 'Email Graph (IMAP)'),
        ('learning', 'Background Review'),
        ('manual', 'Manual Entry'),
        ('skill', 'Learned Skill'),
        ('correction', 'User Correction'),
        ('goal', 'Goal/KPI'),
        ('calendar_event', 'Calendar Event'),
        ('discuss_chat', 'Discuss Chat'),
        ('joplin_note', 'Joplin Note'),
        ('system', 'System Generated'),
    ], string='Source', default='chat', index=True,
       help='Where this memory originated.')
    source_ref = fields.Char(
        string='Source Reference',
        help='Reference to source record, e.g. "joplin.note,42" or "calendar.event,15".')

    # ════════════════════════════════════════════
    # CONTENT — Markdown, mänskligt läsbar
    # ════════════════════════════════════════════
    content = fields.Text(
        string='Memory', required=True,
        help='Memory content in markdown. Human-readable and LLM-friendly. '
             'Once set, content cannot be modified (ADD-only).')
    content_preview = fields.Char(
        string='Preview', compute='_compute_preview', store=False)

    category = fields.Selection([
        ('fact', 'Fact about user'),
        ('preference', 'User preference'),
        ('goal', 'Goal or KPI'),
        ('correction', 'Correction received'),
        ('pattern', 'Behavioral pattern'),
        ('feedback', 'Feedback given'),
        ('context', 'Contextual information'),
        ('mail', 'Email context'),
        ('skill', 'Learned capability'),
        ('insight', 'Insight from conversation'),
        ('policy', 'Company policy'),
    ], string='Category', default='fact', index=True)

    importance = fields.Selection([
        ('low', 'Low'),
        ('medium', 'Medium'),
        ('high', 'High'),
    ], string='Importance', default='medium', index=True)

    # ════════════════════════════════════════════
    # HYBRID SEARCH — pgvector + tsvector
    # ════════════════════════════════════════════
    embedding = fields.Text(
        string='Vector Embedding',
        help='pgvector(1536d) embedding from AI-provider '
             '(OpenAI text-embedding-3-small). '
             'Stored as PostgreSQL vector literal, e.g. "[0.1,0.2,...]". '
             'Set via cron or on-create.')
    # search_vector — GENERATED COLUMN via SQL migration:
    #   ALTER TABLE ai_personal_memory ADD COLUMN search_vector tsvector
    #     GENERATED ALWAYS AS (to_tsvector('swedish', content)) STORED;

    # ════════════════════════════════════════════
    # ENTITY LINKING (mem0-mönster)
    # ════════════════════════════════════════════
    entities = fields.Json(
        string='Extracted Entities',
        help='Entities extracted at creation time for entity boost. '
             'Format: [{"type": "PROPER", "text": "K2"}, ...]')

    # ════════════════════════════════════════════
    # ADD-ONLY — aldrig uppdaterad, bara arkiverad
    # ════════════════════════════════════════════
    create_date = fields.Datetime(
        string='Created', default=fields.Datetime.now, readonly=True)
    archived = fields.Boolean(
        string='Archived', default=False, index=True,
        help='Soft-delete. Memories are never deleted, only archived.')
    archive_date = fields.Datetime(
        string='Archived On', readonly=True)

    # ════════════════════════════════════════════
    # USAGE STATS
    # ════════════════════════════════════════════
    access_count = fields.Integer(
        string='Access Count', default=0,
        help='Number of times this memory was returned in search results.')
    last_accessed = fields.Datetime(string='Last Accessed')
    consolidated = fields.Boolean(
        string='Consolidated', default=False,
        help='Included in consolidated user model.')

    # ════════════════════════════════════════════
    # COMPUTED FIELDS
    # ════════════════════════════════════════════

    @api.depends('content')
    def _compute_preview(self):
        for r in self:
            r.content_preview = (r.content or '')[:120]

    # ════════════════════════════════════════════
    # CONSTRAINTS — ADD-ONLY
    # ════════════════════════════════════════════

    @api.constrains('content')
    def _check_add_only(self):
        """Förhindra ändring av content efter skapande."""
        for r in self:
            if r.create_date and r.id:
                # Jämför med databasvärdet (inte _origin som är internt)
                self.env.cr.execute(
                    'SELECT content FROM ai_personal_memory WHERE id = %s',
                    [r.id])
                row = self.env.cr.fetchone()
                if row and row[0] != r.content:
                    raise UserError(_(
                        'Memory content cannot be modified (ADD-only). '
                        'Create a new memory instead.'))

    # ════════════════════════════════════════════
    # API — HUVUDFUNKTIONER
    # ════════════════════════════════════════════

    @api.model
    def add_memory(self, user_id, content, category='fact', source='chat',
                   coworker_id=None, session_id=None, company_id=None,
                   importance='medium', entities=None, source_ref=None):
        """ADD-only: skapa ett nytt minne. Går inte att uppdatera i efterhand.

        Detta är mem0s ADD-only approach:
        - Extrahera entities från content (om inte angivna)
        - Generera embedding via AI-provider
        - Spara i databasen
        - Låt retrieval avgöra relevans vid sökning

        Args:
            user_id (int): res.users.id — minnet följer PERSONEN
            content (str): Markdown-text med minnet
            category (str): Typ av minne (fact, preference, etc.)
            source (str): Var kommer minnet ifrån
            coworker_id (int, optional): ai.coworker.id
            session_id (int, optional): ai.coworker.session.id
            company_id (int, optional): res.company.id
            importance (str): low, medium, high
            entities (list, optional): Förifyllda entities
            source_ref (str, optional): Referens till källpost

        Returns:
            ai.personal.memory record
        """
        # Validera att user_id finns
        user = self.env['res.users'].browse(user_id)
        if not user.exists():
            raise UserError(_('User %s not found') % user_id)

        # Extrahera entities om inte angivna
        if not entities:
            entities = self._extract_entities(content)

        # Hämta company från user om inte angivet
        if not company_id and user.company_id:
            company_id = user.company_id.id

        # Generera embedding (asynkront — acceptabelt att misslyckas)
        embedding = None
        try:
            embedding = self._generate_embedding(content)
        except Exception as e:
            _logger.warning('Embedding generation failed for memory: %s', e)

        # Skapa minnet (ALLTID ADD, aldrig UPDATE)
        memory = self.create({
            'user_id': user_id,
            'company_id': company_id,
            'source_coworker_id': quest_id,
            'source_session_id': session_id,
            'source': source,
            'source_ref': source_ref,
            'content': content,
            'category': category,
            'importance': importance,
            'embedding': embedding,
            'entities': entities,
        })

        _logger.info(
            'Personal memory added for user %s: %s',
            user.display_name, content[:80])
        return memory

    # ════════════════════════════════════════════
    # HYBRID SEARCH — tre signaler
    # ════════════════════════════════════════════

    @api.model
    def search_for_user(self, user_id, query=None, limit=10, threshold=0.1,
                        include_archived=False, explain=False):
        """Hybrid search över ALLA minnen för en användare.

        Detta är kärnan: oavsett vilken ai.coworker användaren pratar med,
        returneras ALLA relevanta minnen för personen.

        Använder mem0s multi-signal retrieval:
        1. Semantic (pgvector cosine similarity) — om query och embedding finns
        2. BM25 (tsvector full-text, svensk) — om query finns
        3. Entity boost — om query-entities matchar lagrade entities

        Utan query: returnera senaste minnena (fallback).

        Args:
            user_id (int): res.users.id
            query (str, optional): Sökfråga. None = returnera senaste.
            limit (int): Max antal resultat
            threshold (float): Minimum score (0.0-1.0)
            include_archived (bool): Inkludera arkiverade minnen
            explain (bool): Inkludera score_details i resultat

        Returns:
            list[dict]: Sorterade resultat med score
        """
        domain = [('user_id', '=', user_id)]
        if not include_archived:
            domain.append(('archived', '=', False))

        # Utan query: returnera senaste minnena
        if not query or not query.strip():
            records = self.search(domain, limit=limit, order='create_date desc')
            return [{
                'id': r.id,
                'content': r.content,
                'category': r.category,
                'importance': r.importance,
                'source': r.source,
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
            self.env.cr.execute("""
                SELECT id, content, category, importance, source,
                       create_date,
                       1 - (embedding <=> %s::vector) AS semantic_score
                FROM ai_personal_memory
                WHERE user_id = %s
                  AND archived = %s
                  AND embedding IS NOT NULL
                  AND 1 - (embedding <=> %s::vector) >= %s
                ORDER BY semantic_score DESC
                LIMIT %s
            """, (query_embedding, user_id, include_archived,
                  query_embedding, threshold, limit * 4))
            semantic_results = self.env.cr.dictfetchall()

        # ════════════════════════════════════════
        # SIGNAL 2: BM25 (tsvector full-text)
        # ════════════════════════════════════════
        bm25_scores = {}
        try:
            # savepoint: ett ev. SQL-fel (t.ex. saknad search_vector-kolumn i
            # DB:er som inte kört migrationen) abortar annars hela transaktionen
            # → alla efterföljande queries kraschar med InFailedSqlTransaction.
            with self.env.cr.savepoint():
                self.env.cr.execute("""
                    SELECT id,
                           ts_rank(search_vector,
                                   plainto_tsquery('swedish', %s)) AS bm25_score
                    FROM ai_personal_memory
                    WHERE user_id = %s
                      AND archived = %s
                      AND search_vector @@ plainto_tsquery('swedish', %s)
                    ORDER BY bm25_score DESC
                    LIMIT %s
                """, (query, user_id, include_archived, query, limit * 4))
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
        entity_boosts = self._compute_entity_boosts(query_entities, user_id)

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

        # Bygg resultat från semantic + resten
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
                'source': r.get('source'),
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

            # Hämta record-data
            record = self.browse(mem_id)
            if record.exists():
                scored.append({
                    'id': mem_id,
                    'content': record.content,
                    'category': record.category,
                    'importance': record.importance,
                    'source': record.source,
                    'create_date': record.create_date,
                    'score': combined,
                    'score_details': {
                        'semantic': 0.0,
                        'bm25': bm25,
                        'entity_boost': entity,
                    } if explain else None,
                })
            seen_ids.add(mem_id)

        # Sortera och returnera top-k
        scored.sort(key=lambda x: x['score'], reverse=True)
        top_results = scored[:limit]

        # Uppdatera access_count
        top_ids = [r['id'] for r in top_results if r.get('id')]
        if top_ids:
            self.browse(top_ids).write({
                'access_count': fields.Field.column_access_count + 1
                if hasattr(fields.Field, 'column_access_count') else 1,
                'last_accessed': fields.Datetime.now(),
            })

        return top_results

    @api.model
    def search_recent(self, user_id, limit=10):
        """Hämta senaste minnena för en användare (fallback när query saknas)."""
        return self.search_for_user(user_id, query=None, limit=limit)

    # ════════════════════════════════════════════
    # SYSTEM PROMPT INJECTION (Hermes-mönster)
    # ════════════════════════════════════════════

    @api.model
    def build_system_prompt_block(self, user_id, query=None, max_chars=2200):
        """Bygg en system prompt-block för en användare.

        Detta anropas av ai.coworker när den startar en session.
        Resultatet är en "frozen snapshot" — ändras inte under sessionen.

        Format (Hermes-kompatibelt):
        ══════════════════════════════════════════
        USER PROFILE [45% — 1,000/2,200 chars]
        ══════════════════════════════════════════
        § Fakta: ...
        § Preferens: ...

        Args:
            user_id (int): res.users.id
            query (str, optional): Sökfråga för att filtrera relevanta minnen
            max_chars (int): Max antal tecken i blocket

        Returns:
            str: Formatterad markdown-block, eller tom sträng om inga minnen
        """
        # Hämta relevanta minnen
        if query:
            memories = self.search_for_user(user_id, query=query, limit=20)
        else:
            memories = self.search_recent(user_id, limit=20)

        if not memories:
            return ''

        # Bygg markdown-block
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

        header = (
            f"USER PROFILE (who the user is) "
            f"[{pct}% — {chars:,}/{max_chars:,} chars]"
        )
        separator = '═' * 46

        return f"{separator}\n{header}\n{separator}\n{content}"

    # ════════════════════════════════════════════
    # BACKGROUND REVIEW — ADD-only extraction
    # ════════════════════════════════════════════

    @api.model
    def extract_from_session(self, session_id):
        """Extrahera lärdomar från en avslutad session.

        Använder mem0s ADD-only extraction prompt-mönster:
        - Hämta existerande minnen för användaren (deduplicering)
        - Läs sessionens meddelanden
        - EN LLM-extraction → JSON-array av nya minnen
        - ADD alla till ai.personal.memory

        Args:
            session_id (int): ai.coworker.session.id

        Returns:
            int: Antal extraherade minnen
        """
        session = self.env['ai.coworker.session'].browse(session_id)
        if not session.exists():
            return 0

        user_id = session.user_id.id or session.coworker_id.user_id.id
        if not user_id:
            return 0

        coworker_id = session.coworker_id.id

        # Hämta session lines
        lines = session.session_line_ids.sorted('sequence')
        messages = [
            {'role': line.role, 'content': line.content}
            for line in lines if line.content
        ]

        if not messages:
            return 0

        # Hämta existerande minnen för deduplicering
        last_msg = messages[-1]['content'][:200] if messages else ''
        existing = self.search_for_user(user_id, query=last_msg, limit=10)
        existing_texts = [m['content'] for m in existing]

        # LLM-extraction (mem0 ADD-only prompt)
        try:
            new_facts = self._llm_extract_facts(
                messages=messages,
                existing=existing_texts,
            )
        except Exception as e:
            _logger.error('LLM extraction failed for session %s: %s',
                         session_id, e)
            return 0

        # ADD alla nya minnen
        count = 0
        for fact in new_facts:
            text = fact.get('text', '').strip()
            if len(text) < 20:
                continue
            # Kontrollera att det inte redan finns (deduplicering)
            duplicate = any(text[:100] in ex for ex in existing_texts)
            if duplicate:
                continue

            self.add_memory(
                user_id=user_id,
                content=text,
                category=fact.get('category', 'fact'),
                importance=fact.get('importance', 'medium'),
                source='learning',
                coworker_id=coworker_id,
                session_id=session_id,
            )
            count += 1

        _logger.info('Extracted %d memories from session %s', count, session_id)
        return count

    # ════════════════════════════════════════════
    # CRON: CALENDAR EVENT INDEXER
    # ════════════════════════════════════════════

    @api.model
    def cron_index_calendar(self):
        """Indexera dagens kalenderhändelser.

        Körs nattetid. För varje calendar.event:
        - Hitta partner_ids (deltagare)
        - Mappa till res.users
        - Skapa ai.personal.memory med category='context'
        """
        today = date.today()
        events = self.env['calendar.event'].search([
            ('start_date', '>=', today.isoformat()),
            ('stop_date', '<', (today + timedelta(days=1)).isoformat()),
            ('partner_ids', '!=', False),
        ])

        count = 0
        for event in events:
            for partner in event.partner_ids:
                users = self.env['res.users'].search([
                    ('partner_id', '=', partner.id),
                ], limit=1)
                if not users:
                    continue
                user = users[0]

                # Bygg markdown
                content = (
                    f"## Möte: {event.name}\n"
                    f"**Tid:** {event.start_date}\n"
                )
                if event.description:
                    content += f"\n{event.description[:2000]}"

                # Kolla om redan indexerat (idempotens)
                source_ref = f'calendar.event,{event.id}'
                existing = self.search([
                    ('user_id', '=', user.id),
                    ('source_ref', '=', source_ref),
                ], limit=1)

                if not existing:
                    self.add_memory(
                        user_id=user.id,
                        content=content[:5000],
                        category='context',
                        source='calendar_event',
                        source_ref=source_ref,
                        company_id=user.company_id.id,
                    )
                    count += 1

        if count:
            _logger.info('Indexed %d calendar events', count)
        return count

    # ════════════════════════════════════════════
    # CRON: DISCUSS CHAT INDEXER
    # ════════════════════════════════════════════

    @api.model
    def cron_index_chats(self):
        """Indexera gårdagens discuss-konversationer.

        Körs nattetid. För varje mail.message i discuss.channel:
        - Hitta channel_partner_ids
        - Mappa till res.users
        - Skapa ai.personal.memory
        """
        yesterday = date.today() - timedelta(days=1)
        today = date.today()

        messages = self.env['mail.message'].search([
            ('model', '=', 'discuss.channel'),
            ('create_date', '>=', yesterday.strftime('%Y-%m-%d 00:00:00')),
            ('create_date', '<', today.strftime('%Y-%m-%d 00:00:00')),
            ('message_type', '=', 'comment'),
            ('body', '!=', False),
        ])

        count = 0
        for msg in messages:
            channel = self.env['discuss.channel'].browse(msg.res_id or 0)
            if not channel.exists():
                continue

            # Konvertera HTML till markdown
            body_text = self._html_to_text(msg.body or '')

            for partner in channel.channel_partner_ids:
                users = self.env['res.users'].search([
                    ('partner_id', '=', partner.id),
                ], limit=1)
                if not users:
                    continue
                user = users[0]

                content = (
                    f"## Chat: {channel.name}\n"
                    f"**Från:** {msg.author_id.name}\n\n"
                    f"{body_text[:1000]}"
                )

                self.add_memory(
                    user_id=user.id,
                    content=content,
                    category='context',
                    source='discuss_chat',
                    company_id=user.company_id.id,
                )
                count += 1

        if count:
            _logger.info('Indexed %d chat messages', count)
        return count



    # ════════════════════════════════════════════
    # NIGHTLY CRON
    # ════════════════════════════════════════════

    @api.model
    def cron_nightly_index(self):
        """Nattlig full-indexering (körs 02:00 via ir.cron).

        Orchestrerar alla pipelines:
        1. Calendar events
        2. Discuss chats
        3. Joplin notes
        4. Consolidation
        """
        results = {}
        for method_name in ['cron_index_calendar',
                            'cron_index_chats',
                            'cron_daily_consolidation']:
            try:
                results[method_name] = getattr(self, method_name)()
            except Exception as e:
                _logger.error('Nightly cron %s failed: %s', method_name, e)
                results[method_name] = str(e)

        _logger.info('Nightly index complete: %s', results)
        return results

    @api.model
    def cron_daily_consolidation(self):
        """Daglig konsolidering.

        1. Arkivera låg-importanta minnen äldre än 30 dagar
        2. Konsolidera till user_model på användarens identity
        3. Generera embeddings för minnen som saknar
        """
        # 1. Arkivera gammalt låg-important
        cutoff = date.today() - timedelta(days=30)
        old_low = self.search([
            ('importance', '=', 'low'),
            ('archived', '=', False),
            ('create_date', '<', cutoff.strftime('%Y-%m-%d 00:00:00')),
        ])
        archived_count = len(old_low)
        if old_low:
            old_low.write({
                'archived': True,
                'archive_date': fields.Datetime.now(),
            })

        # 2. Uppdatera user_model på identity (per användare)
        users = self.env['res.users'].search([('active', '=', True)])
        for user in users:
            personal_quest = user.personal_coworker_id
            if not personal_quest or not personal_quest.identity_id:
                continue

            memories = self.search([
                ('user_id', '=', user.id),
                ('archived', '=', False),
            ], limit=50)

            if not memories:
                continue

            # Gruppera efter kategori
            categories = {}
            for m in memories:
                cat = m.category or 'fact'
                if cat not in categories:
                    categories[cat] = []
                categories[cat].append(m.content[:200])

            # Bygg user_model
            lines = []
            for cat, facts in sorted(categories.items()):
                unique = list(set(facts))[:5]
                if unique:
                    lines.append(f"## {cat.capitalize()}")
                    for f in unique:
                        lines.append(f"- {f}")

            if lines:
                personal_quest.identity_id.user_model = '\n'.join(lines)[:4000]

        # 3. Generera embeddings för minnen som saknar
        no_emb = self.search([
            ('embedding', '=', False),
            ('archived', '=', False),
        ], limit=200)
        emb_count = 0
        for mem in no_emb:
            try:
                mem.embedding = self._generate_embedding(mem.content)
                emb_count += 1
            except Exception as e:
                _logger.warning(
                    'Embedding generation failed for memory %s: %s',
                    mem.id, e)

        _logger.info(
            'Consolidation: archived %d, embeddings %d',
            archived_count, emb_count)
        return archived_count + emb_count

    # ════════════════════════════════════════════
    # PRIVATA HJÄLPMETODER
    # ════════════════════════════════════════════

    @api.model
    def _generate_embedding(self, text):
        return self._embed_text(text)

    @api.model
    def embed_batch(self, texts):
        """Batch-embeddning — mycket effektivare än en och en.

        Args:
            texts (list[str]): Texter att embedda

        Returns:
            list[str]: PostgreSQL vector-literals eller None för failed
        """
        if not texts:
            return []

        # Truncate each text
        truncated = [t[:8192] for t in texts]

        # Försök via ai.provider med batch
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
        except Exception as e:
            _logger.debug('Batch embedding failed, falling back to single: %s', e)

        # Fallback: embedda en och en
        return [self._embed_text(t) for t in truncated]

    @api.model
    def _embed_text(self, text):
        """Generera embedding via AI-provider.

        Använder samma provider som ai.coworker använder.
        OpenAI text-embedding-3-small (1536 dimensioner).
        Lagrar som PostgreSQL vector-literal: "[0.1,0.2,...]".

        Args:
            text (str): Text att embedda

        Returns:
            str: PostgreSQL vector literal (t.ex. "[0.1,0.2,...]") eller None
        """
        # Försök via ai.provider om tillgängligt
        try:
            Provider = self.env['ai.provider']
            if Provider and hasattr(Provider, '_get_embedding'):
                embedding = Provider._get_embedding(
                    model='text-embedding-3-small',
                    input=text[:8192],
                )
                if embedding and isinstance(embedding, (list, tuple)):
                    # PostgreSQL vector literal: [0.1,0.2,...]
                    return '[' + ','.join(str(v) for v in embedding) + ']'
        except Exception as e:
            _logger.debug('Provider embedding failed: %s', e)

        # Fallback: försök via requests direkt
        try:
            import requests
            # Hitta aktiv provider
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
                    # PostgreSQL vector literal
                    return '[' + ','.join(str(v) for v in embedding) + ']'
        except Exception as e:
            _logger.warning('Direct embedding failed: %s', e)

        return None

    @api.model
    def _extract_entities(self, text):
        """Extrahera entities från text (regex-baserad MVP).

        I produktion: anropa spaCy eller LLM för svensk NER.
        Här: enkel regex-baserad extrahering.

        Args:
            text (str): Text att extrahera entities från

        Returns:
            list[dict]: Entities med type och text
        """
        if not text:
            return []

        entities = []

        # Citattecken-text (specifika termer)
        quoted = re.findall(r'"([^"]+)"', text)
        for q in quoted[:5]:
            entities.append({'type': 'QUOTED', 'text': q.strip()[:50]})

        # Versala ord/förkortningar (K2, K3, BAS, AB, EF, etc.)
        proper = re.findall(
            r'\b([A-ZÅÄÖ][A-ZÅÄÖ0-9]{1,5})\b', text)
        for p in proper[:5]:
            entities.append({'type': 'PROPER', 'text': p})

        # Kontonummer (4+ siffror)
        codes = re.findall(r'\b(\d{4,6})\b', text)
        for c in codes[:3]:
            entities.append({'type': 'CODE', 'text': c})

        # Entity-typer med specifika svenska termer
        finance_terms = [
            'periodiseringsfond', 'avskrivning', 'moms', 'bokslut',
            'resultaträkning', 'balansräkning', 'skatteverket',
            'f-skatt', 'egenavgift', 'K2', 'K3', 'BAS',
        ]
        for term in finance_terms:
            if term.lower() in text.lower():
                entities.append({'type': 'TOPIC', 'text': term})

        return entities

    @staticmethod
    def _normalize_bm25(raw_score):
        """Normalisera BM25-score till [0, 1] med sigmoid (mem0-mönster).

        Använder query-length-adaptiva parametrar.
        """
        if not raw_score or raw_score <= 0:
            return 0.0
        midpoint = 7.0
        steepness = 0.6
        return 1.0 / (1.0 + math.exp(-steepness * (raw_score - midpoint)))

    @api.model
    def _compute_entity_boosts(self, query_entities, user_id):
        """Beräkna entity boost per minne.

        För varje entity i queryn: sök efter minnen med samma entity.

        Args:
            query_entities (list): Entities från queryn
            user_id (int): Användaren att söka inom

        Returns:
            dict: {memory_id: boost_score}
        """
        if not query_entities:
            return {}

        boosts = {}
        entity_texts = [e['text'] for e in query_entities[:8]]

        for entity_text in entity_texts:
            if not entity_text:
                continue

            # Sök efter minnen som innehåller denna entity i entities-json
            self.env.cr.execute("""
                SELECT id
                FROM ai_personal_memory
                WHERE user_id = %s
                  AND archived = FALSE
                  AND entities IS NOT NULL
                  AND entities::text ILIKE %s
                LIMIT 50
            """, (user_id, f'%{entity_text}%'))

            for row in self.env.cr.dictfetchall():
                mem_id = row['id']
                boosts[mem_id] = min(boosts.get(mem_id, 0) + 0.25, 0.5)

        return boosts

    @api.model
    def _llm_extract_facts(self, messages, existing):
        """Anropa LLM för ADD-only extraction.

        Använder mem0s ADDITIVE_EXTRACTION_PROMPT-mönster.

        Args:
            messages (list[dict]): Session messages med role/content
            existing (list[str]): Existerande minnen för deduplicering

        Returns:
            list[dict]: Extraherade fakta med text, category, importance
        """
        import json as json_lib

        # Bygg prompt
        existing_str = '\n'.join(f'- {e[:200]}' for e in existing[:10])
        messages_str = '\n'.join(
            f"[{m['role']}] {m['content'][:500]}"
            for m in messages[-10:]
        )

        prompt = f"""Extract ALL new memorable facts from this conversation.
ADD only — never update or delete existing memories.
Each fact MUST be a self-contained, context-rich statement in Swedish.

Existing memories (DO NOT duplicate these):
{existing_str or '(none)'}

Conversation:
{messages_str}

Return ONLY a JSON object: {{"memory": [{{"text": "...", "category": "fact|preference|goal|correction|pattern", "importance": "low|medium|high"}}]}}"""

        # Anropa LLM via ai.provider
        try:
            Provider = self.env['ai.provider']
            response = Provider._generate(
                model='gpt-4o-mini',  # billig modell räcker
                messages=[{'role': 'user', 'content': prompt}],
                response_format={'type': 'json_object'},
            )
            result = json_lib.loads(response)
            return result.get('memory', [])
        except Exception as e:
            _logger.warning('LLM extraction failed: %s', e)
            return []

    @staticmethod
    def _html_to_text(html):
        """Konvertera HTML till plain text (för chat-body)."""
        if not html:
            return ''
        try:
            from html.parser import HTMLParser
            class MLStripper(HTMLParser):
                def __init__(self):
                    super().__init__()
                    self.reset()
                    self.strict = False
                    self.convert_charrefs = True
                    self.text = []
                def handle_data(self, d):
                    self.text.append(d)
            s = MLStripper()
            s.feed(html)
            return ''.join(s.text).strip()
        except Exception:
            import re
            return re.sub(r'<[^>]+>', '', html).strip()

    @api.model
    def cron_extract_from_discuss(self):
        """Analysera gårdagens channel-meddelanden och extrahera lärdomar.

        Körs nattetid efter cron_index_chats. För varje användare med
        learn_from_discuss=True:
        1. Samla channel-meddelanden från gårdagen
        2. EN LLM-anrop per användare
        3. Spara i ai.personal.memory + uppdatera identity
        """
        yesterday = date.today() - timedelta(days=1)
        today = date.today()
        yesterday_start = yesterday.strftime('%Y-%m-%d 00:00:00')
        today_start = today.strftime('%Y-%m-%d 00:00:00')

        messages = self.env['mail.message'].search([
            ('model', '=', 'discuss.channel'),
            ('create_date', '>=', yesterday_start),
            ('create_date', '<', today_start),
            ('message_type', '=', 'comment'),
            ('body', '!=', False),
            ('author_id', '!=', False),
        ])
        _logger.info("Found %d discuss messages from %s", len(messages), yesterday)

        # Gruppera per användare
        by_user = {}
        for msg in messages:
            users = self.env['res.users'].search([
                ('partner_id', '=', msg.author_id.id),
            ], limit=1)
            if not users or not users.learn_from_discuss:
                continue
            channel = self.env['discuss.channel'].browse(msg.res_id or 0)
            if not channel.exists() or channel.channel_type != 'channel':
                continue

            by_user.setdefault(users.id, []).append(
                f"[{channel.name}] {msg.author_id.name}: {self._html_to_text(msg.body)[:300]}"
            )

        total = 0
        for uid, msgs in by_user.items():
            if len(msgs) < 3:
                continue
            user = self.env['res.users'].browse(uid)
            quest = user.personal_coworker_id
            identity = quest.identity_id if quest else None

            # LLM extraction
            prompt = f"""
            Given these channel messages from {user.name}, extract:
            1. New memories (facts, preferences, goals)
            2. Identity updates (style, user_model)

            Messages:
            {chr(10).join(msgs[:20])}

            Return JSON:
            {{"memories": [{{"content":"...","category":"fact|preference|goal","importance":"low|medium|high"}}],
              "identity_updates": {{"style":"...","user_model":"..."}} }}
            """
            try:
                result = json.loads(self.env['ai.provider']._generate(
                    model='gpt-4o-mini',
                    messages=[{'role': 'user', 'content': prompt}],
                ))
                for mem in result.get('memories', []):
                    self.add_memory(user_id=uid, content=mem['content'],
                        category=mem.get('category','context'), source='discuss_chat',
                        importance=mem.get('importance','medium'), company_id=user.company_id.id)
                    total += 1
                if identity and result.get('identity_updates'):
                    up = result['identity_updates']
                    if up.get('style'): identity.style = up['style']
                    if up.get('user_model'):
                        identity.user_model = (identity.user_model or '') + '\n' + up['user_model']
            except Exception as e:
                _logger.error("Extraction failed for user %s: %s", user.name, e)

        _logger.info("Extracted %d learnings from discuss channels", total)
        return total
