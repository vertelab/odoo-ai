# -*- coding: utf-8 -*-
"""ai.company.memory — Företagsminne som följer organisationen.

LEVEL 1: Indexerad rådata (partners, knowledge, website)
LEVEL 2: Management summary (AI-genererad per funktion)
LEVEL 3: Strategi (OKR, BMC, SWOT)

Kategorier med group_ids för access-styrning per användare.
"""

import logging
from datetime import date, datetime, timedelta

from odoo import models, fields, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class AICompanyMemory(models.Model):
    _name = 'ai.company.memory'
    _description = 'Company Memory — shared knowledge for the organization'
    _order = 'create_date desc'
    _rec_name = 'content_preview'

    _inherit = 'ai.memory.mixin'

    # ════════════════════════════════════════════
    # SCOPE
    # ════════════════════════════════════════════
    company_id = fields.Many2one(
        'res.company', string='Company', required=True, index=True,
        default=lambda self: self.env.company,
        help='The company this memory belongs to.')
    category_id = fields.Many2one(
        'ai.company.memory.category', string='Category', index=True,
        help='Category for access control and grouping.')
    scope = fields.Selection([
        ('public', 'Public'),
        ('restricted', 'Restricted'),
    ], string='Scope', default='public', index=True,
       help='Public: visible to all company users. '
            'Restricted: only users with access to the category.')

    # Source tracking
    user_id = fields.Many2one(
        'res.users', string='Created By', index=True,
        help='Who created this memory.')
    source = fields.Selection([
        ('partner', 'Partner Intelligence'),
        ('knowledge', 'Knowledge Base'),
        ('website', 'Website Crawl'),
        ('strategy', 'Strategy'),
        ('marketing', 'Marketing'),
        ('social', 'Social Media'),
        ('management', 'Management System'),
        ('mgmt_summary', 'Management Summary'),
        ('manual', 'Manual Entry'),
        ('system', 'System Generated'),
    ], string='Source', default='system', index=True)
    source_ref = fields.Char(
        string='Source Reference',
        help='Reference to source record, e.g. "res.partner,42".')
    source_url = fields.Char(
        string='Source URL',
        help='URL if crawled from a website.')

    # ════════════════════════════════════════════
    # CONTENT
    # ════════════════════════════════════════════
    content = fields.Text(
        string='Memory', required=True,
        help='Markdown content. Read-only after creation (ADD-only).')
    content_preview = fields.Char(
        string='Preview', compute='_compute_preview', store=False)
    category = fields.Selection([
        ('customer', 'Customer Intelligence'),
        ('supplier', 'Supplier Intelligence'),
        ('strategy', 'Strategy & OKR'),
        ('marketing', 'Marketing & Brand'),
        ('competitor', 'Competitor Intelligence'),
        ('market', 'Market Intelligence'),
        ('management', 'Management System'),
        ('knowledge', 'Knowledge Base'),
        ('website', 'Website RAG'),
        ('social', 'Social Media'),
        ('hr', 'HR Data'),
        ('finance', 'Financial Data'),
        ('mgmt_summary', 'Management Summary'),
        ('operations', 'Operations'),
    ], string='Content Category', default='knowledge', index=True)

    importance = fields.Selection([
        ('low', 'Low'), ('medium', 'Medium'), ('high', 'High'),
    ], string='Importance', default='medium', index=True)

    # ════════════════════════════════════════════
    # SEARCH
    # ════════════════════════════════════════════
    embedding = fields.Text(
        string='Vector Embedding',
        help='pgvector(1536d) as PostgreSQL vector literal.')
    entities = fields.Json(
        string='Extracted Entities',
        help='Entities extracted for entity linking boost.')
    # search_vector — GENERATED COLUMN via SQL migration

    # ════════════════════════════════════════════
    # ADD-ONLY
    # ════════════════════════════════════════════
    create_date = fields.Datetime(
        string='Created', default=fields.Datetime.now, readonly=True)
    archived = fields.Boolean(
        string='Archived', default=False, index=True)
    archive_date = fields.Datetime(string='Archived On', readonly=True)

    access_count = fields.Integer(string='Access Count', default=0)
    last_accessed = fields.Datetime(string='Last Accessed')

    # ════════════════════════════════════════════
    # COMPUTED
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
        for r in self:
            if r.create_date and r.id:
                self.env.cr.execute(
                    'SELECT content FROM ai_company_memory WHERE id = %s',
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
    def add_company_memory(self, company_id, content, category='knowledge',
                           source='system', source_ref=None, source_url=None,
                           category_id=None, scope='public', user_id=None,
                           importance='medium', entities=None):
        """ADD-only: skapa ett nytt företagsminne.

        Args:
            company_id (int): res.company.id
            content (str): Markdown
            category (str): Content category selection
            source (str): Source selection
            source_ref (str, optional): Reference to source record
            source_url (str, optional): URL if crawled
            category_id (int, optional): ai.company.memory.category for access
            scope (str): 'public' or 'restricted'
            user_id (int, optional): res.users.id who created this
            importance (str): low, medium, high
            entities (list, optional): Pre-extracted entities

        Returns:
            ai.company.memory record
        """
        company = self.env['res.company'].browse(company_id)
        if not company.exists():
            raise UserError(_('Company %s not found') % company_id)

        if not entities:
            entities = self._extract_entities(content)

        embedding = None
        try:
            embedding = self._generate_embedding(content)
        except Exception as e:
            _logger.warning('Embedding generation failed: %s', e)

        memory = self.create({
            'company_id': company_id,
            'category_id': category_id,
            'scope': scope,
            'user_id': user_id or self.env.user.id,
            'source': source,
            'source_ref': source_ref,
            'source_url': source_url,
            'content': content,
            'category': category,
            'importance': importance,
            'embedding': embedding,
            'entities': entities,
        })

        _logger.info(
            'Company memory added for %s: %s',
            company.name, content[:80])
        return memory

    @api.model
    def get_accessible_category_ids(self, user_id=None):
        """Hämta kategori-IDs som en användare har tillgång till.

        Public categories (inga group_ids) + kategorier där användarens
        grupper matchar group_ids.

        Args:
            user_id (int, optional): res.users.id. Default: current user.

        Returns:
            list[int]: Kategori-IDs
        """
        if not user_id:
            user_id = self.env.user.id

        user = self.env['res.users'].browse(user_id)
        user_group_ids = set(user.groups_id.ids)

        # Alla kategorier
        all_cats = self.env['ai.company.memory.category'].search([])
        accessible = []

        for cat in all_cats:
            cat_group_ids = set(cat.group_ids.ids)
            # Public (inga groups) eller användarens grupper matchar
            if not cat_group_ids or (user_group_ids & cat_group_ids):
                accessible.append(cat.id)

        return accessible

    @api.model
    def search_for_company(self, company_id, query=None, limit=10,
                           threshold=0.1, user_id=None, include_archived=False,
                           explain=False, category_ids=None):
        """Hybrid search över företagsminnen, filtrerade per användares access.

        Args:
            company_id (int): res.company.id
            query (str, optional): Sökfråga
            limit (int): Max resultat
            threshold (float): Minimum score
            user_id (int, optional): Användare för access-filtrering
            include_archived (bool): Inkludera arkiverade
            explain (bool): Inkludera score_details
            category_ids (list, optional): Begränsa till specifika kategorier

        Returns:
            list[dict]: Sorterade resultat
        """
        domain = [('company_id', '=', company_id)]

        # Access-filtrering
        accessible_ids = None
        if user_id:
            accessible_ids = self.get_accessible_category_ids(user_id)
        elif category_ids:
            accessible_ids = category_ids

        if accessible_ids is not None:
            domain.append(('category_id', 'in', accessible_ids))
        elif category_ids:
            domain.append(('category_id', 'in', category_ids))

        return self._search_memory(
            domain=domain, query=query, limit=limit,
            threshold=threshold, include_archived=include_archived,
            explain=explain)

    @api.model
    def search_recent(self, company_id, user_id=None, limit=10):
        """Hämta senaste företagsminnena."""
        domain = [('company_id', '=', company_id)]
        accessible_ids = None
        if user_id:
            accessible_ids = self.get_accessible_category_ids(user_id)
        if accessible_ids is not None:
            domain.append(('category_id', 'in', accessible_ids))

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

    # ════════════════════════════════════════════
    # SYSTEM PROMPT INJECTION
    # ════════════════════════════════════════════

    @api.model
    def build_system_prompt_block(self, company_id, user_id=None,
                                   max_chars=2000):
        """Bygg Hermes-kompatibel system prompt block för company memory.

        Injicerar:
        1. Management summary (Level 2) — om finns
        2. Strategy (Level 3) — om finns

        Indexerad data (Level 1) är tillgänglig via hybrid search.

        Args:
            company_id (int): res.company.id
            user_id (int, optional): Användare för access-filtrering
            max_chars (int): Max tecken

        Returns:
            str: Formatterad block eller tom sträng
        """
        parts = []

        # Level 2: Management summary
        summary = self.search_for_company(
            company_id=company_id, user_id=user_id,
            query='mgmt_summary', limit=5)
        summary_block = self._build_memory_block(
            summary, max_chars // 2, 'MANAGEMENT SUMMARY')
        if summary_block:
            parts.append(summary_block)

        # Level 3: Strategy
        strategy_domain = [
            ('company_id', '=', company_id),
            ('category', 'in', ['strategy']),
        ]
        accessible = None
        if user_id:
            accessible = self.get_accessible_category_ids(user_id)

        strategy_records = self.search(
            strategy_domain + ([('category_id', 'in', accessible)]
                               if accessible else []),
            limit=10, order='create_date desc')
        strategy_data = [{
            'id': r.id, 'content': r.content,
            'category': r.category, 'importance': r.importance,
            'create_date': r.create_date, 'score': 1.0,
        } for r in strategy_records]

        strategy_block = self._build_memory_block(
            strategy_data, max_chars // 2, 'STRATEGY')
        if strategy_block:
            parts.append(strategy_block)

        if not parts:
            return ''

        return '\n\n'.join(parts)

    # ════════════════════════════════════════════
    # CRON: NIGHTLY CONSOLIDATION
    # ════════════════════════════════════════════

    @api.model
    def cron_nightly_consolidation(self):
        """Nattlig konsolidering av company memory.

        1. Arkivera låg-importanta minnen äldre än 60 dagar
        2. Generera embeddings för minnen som saknar
        """
        cutoff = date.today() - timedelta(days=60)
        old_low = self.search([
            ('importance', '=', 'low'),
            ('archived', '=', False),
            ('create_date', '<', cutoff.strftime('%Y-%m-%d 00:00:00')),
        ])
        archived = len(old_low)
        if old_low:
            old_low.write({
                'archived': True,
                'archive_date': fields.Datetime.now(),
            })

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
                    'Embedding failed for company memory %s: %s', mem.id, e)

        _logger.info(
            'Company memory consolidation: archived %d, embeddings %d',
            archived, emb_count)
        return {'archived': archived, 'embeddings': emb_count}
