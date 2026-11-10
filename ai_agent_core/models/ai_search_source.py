# -*- coding: utf-8 -*-
"""Sökkällor — vilka backends en AI Medarbetares minnessökning får slå i.

Fas 13.2 i okf-recall-path. Modellen är en *katalog*, inte en implementation:
`backend` är nyckeln som `multi_source_recall` slår upp i sin dispatch-tabell.
En källa vars backend ännu inte är inkopplad får inte se ut som om den
söker — därför `is_wired` (se nedan).
"""

from odoo import api, fields, models
from odoo.exceptions import ValidationError


class AiSearchSource(models.Model):
    _name = 'ai.search.source'
    _description = 'Search Source (memory backend)'
    _order = 'sequence, name'

    name = fields.Char('Namn', required=True, translate=True)
    code = fields.Char(
        'Kod', required=True, index=True,
        help='Stable technical key, e.g. okf_concept. Used in M2M and in logs; must not change once used.')
    backend = fields.Char(
        'Backend-nyckel', required=True,
        help='The key multi_source_recall dispatches on. Must exist in its table — otherwise the search degrades with a warning instead of silently skipping the source.')
    description = fields.Text('Beskrivning')
    sequence = fields.Integer('Sekvens', default=10)
    active = fields.Boolean('Aktiv', default=True)

    # ── Ärlighetsfältet ─────────────────────────────────────────────────
    is_wired = fields.Boolean(
        'Inkopplad', default=False, readonly=True,
        help='Set by the code that owns the backend — never by the user. A source with is_wired=False must NOT be selectable in a strategy that claims to search: it would only appear in search_sources without contributing a single row.')
    wired_note = fields.Char(
        'Status', readonly=True,
        help='Why the source is or is not wired in. E.g. "the AGE graph odoo_mind has 0 nodes".')

    _sql_constraints = [
        ('code_uniq', 'unique(code)', 'The search source code must be unique.'),
    ]

    @api.constrains('backend')
    def _check_backend_known(self):
        known = self._known_backends()
        for rec in self:
            if rec.backend and rec.backend not in known:
                raise ValidationError(
                    'Unknown backend key: %s. Known: %s' % (
                        rec.backend, ', '.join(sorted(known))))

    @api.model
    def _known_backends(self):
        """Backends som multi_source_recall faktiskt kan dispatca till.

        Hålls som en klassmetod så att både constrainen och sökvägen läser
        SAMMA lista. Två listor hade glidit isär (det är mönstret från
        design.md §15: skrivsidan byggdes, lässidan antogs).
        """
        return {'okf_concept', 'ai_memory', 'graph', 'documents'}

    @api.model
    def _seed_sources(self):
        """Skapa/uppdatera katalogen. Idempotent — körs från data-XML.

        `is_wired` sätts HÄR och inte i XML:en, eftersom den ska spegla
        kodens verkliga förmåga vid den tidpunkt modulen laddas.
        """
        # endast okf_concept är inkopplad (fas 12); de andra är katalogposter
        # som beskriver vad som SKA byggas — de får inte låtsas söka.
        sources = [
            {
                'code': 'okf_concept',
                'name': 'OKF-koncept',
                'backend': 'okf_concept',
                'sequence': 10,
                'is_wired': True,
                'wired_note': 'Inkopplad via _okf_search (fas 12).',
                'description': 'Hybrid search (vector + Swedish BM25) over ai.okf.concept, deduplicated per concept.',
            },
            {
                'code': 'ai_memory',
                'name': 'Minnesrader',
                'backend': 'ai_memory',
                'sequence': 20,
                'is_wired': True,
                'wired_note': 'Inkopplad via _search_chunks (ai-memory-pgvector).',
                'description': 'Search in ai.memory artifacts: pgvector cosine + Swedish BM25 over ai.memory.chunk. Ownership is decided by the link (ai.agent.memory).',
            },
            {
                'code': 'graph',
                'name': 'Kunskapsgraf (AGE)',
                'backend': 'graph',
                'sequence': 30,
                'is_wired': False,
                'wired_note': 'AGE-grafen odoo_mind finns men har 0 noder.',
                'description': 'Graph enrichment via graph.executor.cypher. The catalog honesty field: the graph is empty in operation, so enrichment cannot carry an answer.',
            },
            {
                'code': 'documents',
                'name': 'Dokument-RAG',
                'backend': 'documents',
                'sequence': 40,
                'is_wired': False,
                'wired_note': '11 packages in requirements.txt are not installed (faiss-cpu, sentence_transformers).',
                'description': 'Vector RAG over ir.attachment. Catalog entry only — see phase 10.',
            },
        ]
        for vals in sources:
            existing = self.search([('code', '=', vals['code'])], limit=1)
            if existing:
                existing.write(vals)
            else:
                self.create(vals)
