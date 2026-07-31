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
        # ── OKF-first (tasks 7.1/7.4) ──
        Okf = self.env['ai.okf.concept']
        okf_count = Okf.search_count([
            ('scope', '=', 'company'),
            ('owner_company_id', '=', company_id),
            ('archived', '=', False),
            ('status', '!=', 'superseded'),
        ])
        if okf_count:
            try:
                return Okf._okf_build_system_prompt_block(
                    'company', company_id, query=None,
                    max_chars=max_chars, include_level1=False)
            except Exception as e:
                _logger.warning('OKF system prompt block failed, '
                                'fallback till legacy: %s', e)

        # ══ Legacy-fallback (före migrering) ══
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


    # ════════════════════════════════════════════
    # OKF-INDEXERARE (tasks 7.3)
    # Skriver ai.okf.concept med owner_company_id + attribution.
    # Alla är idempotenta: _okf_upsert skapar ny version vid re-index.
    # ════════════════════════════════════════════

    @api.model
    def _okf_company_id(self, company_id=None):
        return company_id or self.env.company.id

    def _okf_index_partner_batch(self, partners, company_id, kind_label):
        """Indexera res.partner-rader till ai.okf.concept."""
        Okf = self.env['ai.okf.concept']
        company_id = self._okf_company_id(company_id)
        n = 0
        for p in partners:
            try:
                lines = []
                if p.name:
                    lines.append(f"- {p.name}")
                if p.function:
                    lines.append(f"- Roll: {p.function}")
                if p.email:
                    lines.append(f"- E-post: {p.email}")
                if p.phone:
                    lines.append(f"- Telefon: {p.phone}")
                if p.comment:
                    lines.append(f"- Kommentar: {p.comment}")
                if p.industry_id:
                    lines.append(f"- Bransch: {p.industry_id.name}")
                if not lines:
                    continue
                summary = (f"### {kind_label}: {p.name}\n"
                           + "\n".join(lines))
                source_ref = f"res.partner,{p.id}"
                Okf._okf_upsert(
                    artifact_type='partner',
                    concept_key=f"partner,{p.id}",
                    summary=summary,
                    title=f"{kind_label}: {p.name}",
                    attribution=[{'line': 1,
                                  'source_ref': source_ref}],
                    source_ref=source_ref,
                    owner_company_id=company_id,
                    generated_by='cron_partner_indexer',
                )
                n += 1
            except Exception as e:
                _logger.warning('Partner indexering misslyckades för %s: %s',
                                p.id, e)
        return n

    @api.model
    def cron_index_partners(self, company_id=None, limit=None):
        """Indexera kunder (customer_rank>0) → ai.okf.concept (task 7.3)."""
        domain = [('customer_rank', '>', 0), ('active', '=', True)]
        if limit:
            partners = self.env['res.partner'].search(domain, limit=limit)
        else:
            partners = self.env['res.partner'].search(domain)
        n = self._okf_index_partner_batch(
            partners, company_id, 'Kund')
        _logger.info('OKF partner-indexerare: %s kunder indexerade', n)
        return {'kind': 'partner', 'indexed': n}

    @api.model
    def cron_index_suppliers(self, company_id=None, limit=None):
        """Indexera leverantörer (supplier_rank>0) → OKF (task 7.3)."""
        domain = [('supplier_rank', '>', 0), ('active', '=', True)]
        if limit:
            partners = self.env['res.partner'].search(domain, limit=limit)
        else:
            partners = self.env['res.partner'].search(domain)
        n = self._okf_index_partner_batch(
            partners, company_id, 'Leverantör')
        _logger.info('OKF leverantörs-indexerare: %s leverantörer indexerade', n)
        return {'kind': 'supplier', 'indexed': n}

    @api.model
    def cron_index_knowledge(self, company_id=None):
        """Indexera kunskapsartiklar → OKF (task 7.3)."""
        company_id = self._okf_company_id(company_id)
        Okf = self.env['ai.okf.concept']
        n = 0
        # knowledge-modulen är inte installerad här — guard
        for model_name, title_field, body_field in [
            ('knowledge.article', 'title', 'body'),
        ]:
            if model_name not in self.env:
                _logger.debug('OKF knowledge-indexerare: modellen %s '
                              'saknas — hoppar över', model_name)
                continue
            Model = self.env[model_name]
            if not hasattr(Model, 'search'):
                continue
            for art in Model.search([]):
                try:
                    title = getattr(art, title_field, '') or ''
                    body = getattr(art, body_field, '') or ''
                    if not body:
                        continue
                    source_ref = f"{model_name},{art.id}"
                    Okf._okf_upsert(
                        artifact_type='knowledge',
                        concept_key=f"knowledge,{art.id}",
                        summary=body,
                        title=title or f"Kunskapsartikel {art.id}",
                        attribution=[{'line': 1, 'source_ref': source_ref}],
                        source_ref=source_ref,
                        owner_company_id=company_id,
                        generated_by='cron_knowledge_indexer',
                    )
                    n += 1
                except Exception as e:
                    _logger.warning('Knowledge-indexering misslyckades '
                                    'för %s: %s', art.id, e)
        _logger.info('OKF knowledge-indexerare: %s artiklar indexerade', n)
        return {'kind': 'knowledge', 'indexed': n}

    @api.model
    def cron_index_dms(self, company_id=None):
        """Indexera DMS-dokument → OKF (task 7.3).

        I avsaknad av dms-modulen indexeras ir.attachment med text-typ
        (pdf/docx/txt/md) som dokumentkoncept.
        """
        company_id = self._okf_company_id(company_id)
        Okf = self.env['ai.okf.concept']
        n = 0
        if 'dms.file' in self.env:
            Model = self.env['dms.file']
            for doc in Model.search([]):
                try:
                    name = doc.name or f"DMS {doc.id}"
                    content = getattr(doc, 'content', '') or ''
                    if not content:
                        continue
                    source_ref = f"dms.file,{doc.id}"
                    Okf._okf_upsert(
                        artifact_type='document',
                        concept_key=f"dms,{doc.id}",
                        summary=content,
                        title=name,
                        attribution=[{'line': 1, 'source_ref': source_ref}],
                        source_ref=source_ref,
                        owner_company_id=company_id,
                        generated_by='cron_dms_indexer',
                    )
                    n += 1
                except Exception as e:
                    _logger.warning('DMS-indexering misslyckades %s: %s',
                                    doc.id, e)
        else:
            # Fallback: text-bilagor (ir.attachment) — begränsa till
            # text-liknande mimetyper för att inte drunkna i binärfiler
            _logger.info('OKF DMS-indexerare: dms.file saknas — '
                         'indexerar ir.attachment (text)')
            domain = [
                ('mimetype', 'in',
                 ['text/plain', 'text/markdown', 'text/csv',
                  'application/pdf']),
                ('res_model', 'in', [False, 'res.partner',
                                     'crm.lead', 'sale.order']),
            ]
            docs = self.env['ir.attachment'].search(domain, limit=500)
            for doc in docs:
                try:
                    content = ''
                    try:
                        content = doc._index() if hasattr(doc, '_index') \
                            else doc.raw.decode('utf-8', errors='ignore')
                    except Exception:
                        pass
                    if not content or len(content) < 20:
                        continue
                    source_ref = f"ir.attachment,{doc.id}"
                    Okf._okf_upsert(
                        artifact_type='document',
                        concept_key=f"attachment,{doc.id}",
                        summary=content[:4000],
                        title=doc.name or f"Bilaga {doc.id}",
                        attribution=[{'line': 1, 'source_ref': source_ref}],
                        source_ref=source_ref,
                        owner_company_id=company_id,
                        generated_by='cron_dms_indexer',
                    )
                    n += 1
                except Exception as e:
                    _logger.warning('Attachment-indexering misslyckades '
                                    '%s: %s', doc.id, e)
        _logger.info('OKF DMS-indexerare: %s dokument indexerade', n)
        return {'kind': 'dms', 'indexed': n}

    @api.model
    def cron_index_website(self, company_id=None):
        """Indexera websidor (website_page) → OKF (task 7.3)."""
        company_id = self._okf_company_id(company_id)
        Okf = self.env['ai.okf.concept']
        n = 0
        if 'website.page' not in self.env:
            _logger.debug('OKF website-indexerare: website.page saknas')
            return {'kind': 'website', 'indexed': 0}
        pages = self.env['website.page'].search([
            ('is_published', '=', True),
        ])
        for page in pages:
            try:
                url = page.url or f"/page/{page.id}"
                title = url
                # Innehåll hämtas från den associerade ir.ui.view (arch_db)
                content = ''
                view = page.view_id if hasattr(page, 'view_id') else None
                if view and hasattr(view, 'arch_db'):
                    content = self._html_to_text(view.arch_db)
                elif hasattr(page, 'body_arch'):
                    content = self._html_to_text(page.body_arch)
                if not content or len(content) < 20:
                    continue
                source_ref = f"website.page,{page.id}"
                Okf._okf_upsert(
                    artifact_type='website',
                    concept_key=f"website,{page.id}",
                    summary=content[:4000],
                    title=title,
                    attribution=[{'line': 1, 'source_ref': source_ref}],
                    source_ref=source_ref,
                    owner_company_id=company_id,
                    generated_by='cron_website_indexer',
                )
                n += 1
            except Exception as e:
                _logger.warning('Website-indexering misslyckades %s: %s',
                                page.id, e)
        _logger.info('OKF website-indexerare: %s sidor indexerade', n)
        return {'kind': 'website', 'indexed': n}

    @api.model
    def cron_index_strategy(self, company_id=None):
        """Indexera strategi (legacy ai.company.memory strategy) → OKF.

        Task 7.3 + 7.14: strategy-kategorin i den gamla tabellen läses
        och skrivs till ai.okf.concept; gamla tabellen skrivs inte längre.
        """
        company_id = self._okf_company_id(company_id)
        Okf = self.env['ai.okf.concept']
        n = 0
        legacy = self.search([
            ('company_id', '=', company_id),
            ('category', 'in', ['strategy']),
        ])
        for mem in legacy:
            try:
                key = f"strategy,{mem.id}"
                source_ref = f"ai.company.memory,{mem.id}"
                Okf._okf_upsert(
                    artifact_type='strategy',
                    concept_key=key,
                    summary=mem.content or '',
                    title=(mem.content or 'Strategi')[:80],
                    attribution=[{'line': 1, 'source_ref': source_ref}],
                    source_ref=source_ref,
                    owner_company_id=company_id,
                    generated_by='cron_strategy_indexer',
                )
                n += 1
            except Exception as e:
                _logger.warning('Strategi-indexering misslyckades %s: %s',
                                mem.id, e)
        _logger.info('OKF strategy-indexerare: %s strategier indexerade', n)
        return {'kind': 'strategy', 'indexed': n}

    @api.model
    def cron_generate_management_summary(self, company_id=None):
        """Generera management summary från OKF-koncept → Level 2.

        Task 7.3/7.4: sammanfattningen skrivs som concept_key
        'mgmt_summary' med artifact type 'mgmt_summary' så att
        _okf_build_system_prompt_block hittar den först.
        """
        company_id = self._okf_company_id(company_id)
        Okf = self.env['ai.okf.concept']
        # Sammanställ befintliga koncept till en sammanfattning
        concepts = Okf.search([
            ('scope', '=', 'company'),
            ('owner_company_id', '=', company_id),
            ('archived', '=', False),
            ('status', '!=', 'superseded'),
        ], limit=200)
        if not concepts:
            return {'kind': 'management_summary', 'indexed': 0}

        # Gruppera efter artifact type
        by_type = {}
        for c in concepts:
            atype = c.artifact_type_id.name or 'övrigt'
            by_type.setdefault(atype, []).append(c)

        lines = []
        for atype, items in sorted(by_type.items()):
            lines.append(f"## {atype.title()}")
            for c in items[:20]:
                snippet = (c.summary or c.title or '')[:200].replace(
                    '\n', ' ')
                lines.append(f"- {snippet}")
        summary = '\n'.join(lines)
        source_ref = f"ai.company.memory,{company_id}"
        Okf._okf_upsert(
            artifact_type='mgmt_summary',
            concept_key='mgmt_summary',
            summary=summary,
            title='Ledningssammanfattning (autogenererad)',
            attribution=[{'line': 1, 'source_ref': source_ref}],
            source_ref=source_ref,
            owner_company_id=company_id,
            generated_by='cron_mgmt_summary',
        )
        _logger.info('OKF management-summary genererad (%s koncept)',
                     len(concepts))
        return {'kind': 'management_summary', 'indexed': 1}
