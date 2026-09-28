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
    _description = 'Sökkälla (minnesbackend)'
    _order = 'sequence, name'

    name = fields.Char('Namn', required=True, translate=True)
    code = fields.Char(
        'Kod', required=True, index=True,
        help='Stabil teknisk nyckel, t.ex. okf_concept. Används i M2M och '
             'i loggar; får inte ändras när den väl använts.')
    backend = fields.Char(
        'Backend-nyckel', required=True,
        help='Nyckeln som multi_source_recall dispatcher på. Måste finnas i '
             'dess tabell — annars degraderar sökningen med en varning i '
             'stället för att tyst hoppa över källan.')
    description = fields.Text('Beskrivning')
    sequence = fields.Integer('Sekvens', default=10)
    active = fields.Boolean('Aktiv', default=True)

    # ── Ärlighetsfältet ─────────────────────────────────────────────────
    is_wired = fields.Boolean(
        'Inkopplad', default=False, readonly=True,
        help='Sätts av koden som äger backenden — aldrig av användaren. '
             'En källa med is_wired=False får INTE väljas i en strategi som '
             'utger sig för att söka: den skulle bara synas i '
             'search_sources utan att bidra med en enda rad.')
    wired_note = fields.Char(
        'Status', readonly=True,
        help='Varför källan är/ inte är inkopplad. T.ex. "AGE-grafen '
             'odoo_mind har 0 noder".')

    _sql_constraints = [
        ('code_uniq', 'unique(code)', 'Sökkällans kod måste vara unik.'),
    ]

    @api.constrains('backend')
    def _check_backend_known(self):
        known = self._known_backends()
        for rec in self:
            if rec.backend and rec.backend not in known:
                raise ValidationError(
                    'Okänd backend-nyckel: %s. Kända: %s' % (
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
                'description': 'Hybridsökning (vektor + svensk BM25) över '
                               'ai.okf.concept, dedupnad per koncept.',
            },
            {
                'code': 'ai_memory',
                'name': 'Minnesrader',
                'backend': 'ai_memory',
                'sequence': 20,
                'is_wired': False,
                'wired_note': 'Ännu inte kopplad i multi_source_recall.',
                'description': 'Sökning i ai.memory / ai.personal.memory / '
                               'ai.company.memory. Endast katalogpost.',
            },
            {
                'code': 'graph',
                'name': 'Kunskapsgraf (AGE)',
                'backend': 'graph',
                'sequence': 30,
                'is_wired': False,
                'wired_note': 'AGE-grafen odoo_mind finns men har 0 noder.',
                'description': 'Grafberikning via graph.executor.cypher. '
                               'Katalogens ärlighetsfält: grafen är tom i '
                               'drift, så berikning kan inte bära ett svar.',
            },
            {
                'code': 'documents',
                'name': 'Dokument-RAG',
                'backend': 'documents',
                'sequence': 40,
                'is_wired': False,
                'wired_note': '11 paket i requirements.txt är inte '
                              'installerade (faiss-cpu, sentence_transformers).',
                'description': 'Vektor-RAG över ir.attachment. Endast '
                               'katalogpost — se fas 10.',
            },
        ]
        for vals in sources:
            existing = self.search([('code', '=', vals['code'])], limit=1)
            if existing:
                existing.write(vals)
            else:
                self.create(vals)
