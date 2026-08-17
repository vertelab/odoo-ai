# -*- coding: utf-8 -*-
"""res.company — Company memory integration."""

from odoo import models, fields, api
import logging

_logger = logging.getLogger(__name__)


class ResCompany(models.Model):
    _inherit = 'res.company'

    # ── Company Identity (ported from ai_agent) ──
    company_mission = fields.Html(
        'Our Mission',
        help='The company mission statement. Displayed to AI agents '
             'as context for decision-making.')
    company_values = fields.Html(
        'Our Values',
        help='The company values. Displayed to AI agents '
             'as context for decision-making.')
    company_mission_last_review = fields.Datetime('Mission Last Reviewed')
    company_values_last_review = fields.Datetime('Values Last Reviewed')

    # ── Website RAG ──
    website_rag_attachment_id = fields.Many2one(
        'ir.attachment', string='Website RAG Attachment',
        readonly=True, copy=False,
        help='Latest website RAG index as ir.attachment on the partner record.')
    website_rag_last_index = fields.Datetime(
        'Website RAG Last Index', readonly=True, copy=False,
        help='When the website was last crawled for RAG.')

    # ── PWA (mobilapp) branding ──
    # pwa_icon/pwa_app_name/pwa_icon_bytes ligger nu i web_pwa_push
    # (odoo-web) — se /pwa/manifest/ai-chat och res.company där.

    # ── Company Memory ──
    company_memory_ids = fields.One2many(
        'ai.company.memory', 'company_id',
        string='Company Memories',
        help='All company memories.')
    company_memory_count = fields.Integer(
        string='Memory Count',
        compute='_compute_company_memory_count')

    @api.depends('company_memory_ids')
    def _compute_company_memory_count(self):
        for r in self:
            r.company_memory_count = len(r.company_memory_ids)

    def action_open_company_memory(self):
        """Smart button: öppna företagets minnen."""
        self.ensure_one()
        return {
            'name': 'Company Memories',
            'type': 'ir.actions.act_window',
            'res_model': 'ai.company.memory',
            'view_mode': 'list,form',
            'target': 'current',
            'domain': [('company_id', '=', self.id)],
            'context': {'default_company_id': self.id},
        }

    # ─────────────────────────────────────────────
    # Website RAG
    # ─────────────────────────────────────────────
    def _crawl_website(self, url, max_depth=3):
        """Crawl a website and return markdown with page content.

        Args:
            url (str): Starting URL
            max_depth (int): Max crawl depth

        Returns:
            str: All pages as markdown, one section per URL
        """
        import httpx
        from bs4 import BeautifulSoup
        from urllib.parse import urlparse, urljoin
        import re
        import asyncio

        visited = set()
        to_visit = [(url, 0)]
        pages = []
        domain = urlparse(url).netloc

        while to_visit and len(visited) < 100:
            page_url, depth = to_visit.pop(0)
            if page_url in visited or depth > max_depth:
                continue

            try:
                # Use existing sync HTTP client
                r = httpx.get(page_url, timeout=15, follow_redirects=True,
                            headers={'User-Agent': 'Vertel-OdooMind/1.0'})
                r.raise_for_status()
            except Exception as e:
                _logger.warning('Failed to fetch %s: %s', page_url, e)
                visited.add(page_url)
                continue

            visited.add(page_url)
            soup = BeautifulSoup(r.text, 'html.parser')

            # Remove script, style, nav, footer tags
            for tag in soup(['script', 'style', 'nav', 'footer', 'header',
                           'aside', 'noscript']):
                tag.decompose()

            # Extract text
            text = soup.get_text(separator='\n', strip=True)
            text = re.sub(r'\n{3,}', '\n\n', text)  # Normalize whitespace

            if text.strip():
                pages.append(f"## {page_url}\n\n{text}\n")

            # Find internal links
            if depth < max_depth:
                for link in soup.find_all('a', href=True):
                    href = urljoin(page_url, link['href'])
                    parsed = urlparse(href)
                    if parsed.netloc == domain and not parsed.fragment:
                        # Remove anchors
                        clean = href.split('#')[0]
                        if clean not in visited and clean not in [v[0] for v in to_visit]:
                            to_visit.append((clean, depth + 1))

            # Rate limit: 1 req/s
            import time
            time.sleep(1)

        return '\n\n'.join(pages) if pages else ''

    def _index_website(self):
        """Crawl company website and create RAG attachment."""
        self.ensure_one()
        partner = self.partner_id
        website = partner.website
        if not website:
            raise ValueError('No website URL configured for the company partner')

        # Ensure URL has scheme
        if not website.startswith(('http://', 'https://')):
            website = 'https://' + website

        # Crawl
        markdown_content = self._crawl_website(website)

        if not markdown_content:
            _logger.warning('Website crawl produced no content for %s', website)
            return

        # Create or update attachment on partner
        domain_name = website.replace('https://', '').replace('http://', '').split('/')[0]
        attachment_vals = {
            'name': f'website_rag_{domain_name}.md',
            'res_model': 'res.partner',
            'res_id': partner.id,
            'mimetype': 'text/markdown',
            'raw': markdown_content.encode('utf-8'),
            'description': f'Website RAG index for {website}, created {fields.Datetime.now()}',
        }

        # Archive previous attachment
        if self.website_rag_attachment_id:
            self.website_rag_attachment_id.sudo().unlink()

        attachment = self.env['ir.attachment'].sudo().create(attachment_vals)

        # Update company fields
        self.write({
            'website_rag_attachment_id': attachment.id,
            'website_rag_last_index': fields.Datetime.now(),
        })

        # Create/update company memory entry
        memory = self.env['ai.company.memory'].search([
            ('company_id', '=', self.id),
            ('source', '=', 'website'),
        ], limit=1)
        if memory:
            memory.write({
                'content': f'Website RAG updated: {website}\n\n{markdown_content[:500]}...',
                'source_url': website,
            })
        else:
            self.env['ai.company.memory'].create({
                'company_id': self.id,
                'content': f'Website RAG: {website}\n\n{markdown_content[:500]}...',
                'source': 'website',
                'source_url': website,
                'scope': 'public',
            })

        _logger.info('Website RAG index complete for %s (%d chars)',
                     website, len(markdown_content))

    def _suggest_identity(self):
        """Use AI to suggest updated mission and values from website RAG."""
        self.ensure_one()

        if not self.website_rag_attachment_id:
            raise ValueError('No website RAG found. Index the website first.')

        # Build prompt
        rag_attachment = self.website_rag_attachment_id.sudo()
        rag_content = rag_attachment.raw.decode('utf-8') if rag_attachment.raw else ''

        lines = ['Du är en konsult som hjälper företag att formulera sin mission och värderingar.']
        lines.append('')

        if self.company_mission:
            from odoo.tools import html2plaintext
            current_mission = html2plaintext(self.company_mission)
            lines.append('## Nuvarande mission')
            lines.append(current_mission)
            lines.append('')

        if self.company_values:
            from odoo.tools import html2plaintext
            current_values = html2plaintext(self.company_values)
            lines.append('## Nuvarande values')
            lines.append(current_values)
            lines.append('')

        lines.append('## Innehåll från företagets webbplats')
        lines.append(rag_content[:8000])  # Limit context
        lines.append('')
        lines.append('Föreslå uppdaterad mission och values baserat på webbplatsens innehåll.')
        lines.append('Bygg vidare på befintlig mission/values om de finns.')
        lines.append('')
        lines.append('Svara exakt i detta format:')
        lines.append('MISSION:')
        lines.append('<mission text>')
        lines.append('VALUES:')
        lines.append('<values text>')

        prompt = '\n'.join(lines)

        try:
            # Use the default AI provider to generate suggestion
            provider = self.env['ai.provider'].search([('status', '=', 'confirmed')], limit=1)
            if not provider:
                _logger.error('No active AI provider found for identity suggestion')
                return

            result = provider._chat_completion([{
                'role': 'user',
                'content': prompt,
            }], model_id=None, stream=False)

            response_text = result.get('content', '')

            # Parse response
            mission = ''
            values = ''
            in_mission = False
            in_values = False
            for line in response_text.split('\n'):
                if line.strip().upper().startswith('MISSION:'):
                    in_mission = True
                    in_values = False
                    mission = line.split(':', 1)[1].strip() if ':' in line else ''
                elif line.strip().upper().startswith('VALUES:'):
                    in_mission = False
                    in_values = True
                    values = line.split(':', 1)[1].strip() if ':' in line else ''
                elif in_mission:
                    mission += ' ' + line.strip()
                elif in_values:
                    values += ' ' + line.strip()

            now = fields.Datetime.now()
            update_vals = {}
            if mission.strip():
                update_vals['company_mission'] = f'<p>{mission.strip()}</p>'
                update_vals['company_mission_last_review'] = now
            if values.strip():
                update_vals['company_values'] = f'<p>{values.strip()}</p>'
                update_vals['company_values_last_review'] = now

            if update_vals:
                self.write(update_vals)
                _logger.info('Identity suggestion applied for %s', self.name)

        except Exception as e:
            _logger.error('Identity suggestion failed: %s', e)

    # ── Organization Goals (OKR) ──
    company_objective_ids = fields.One2many(
        'ai.org.goal', compute='_compute_company_objectives',
        string='Företagsmål',
        help='Mål på company-nivå (ai.org.goal level=company).')

    @api.depends('company_objective_ids')
    def _compute_company_objectives(self):
        for company in self:
            company.company_objective_ids = self.env['ai.org.goal'].search([
                ('level', '=', 'company'),
                ('company_id', '=', company.id),
            ])
