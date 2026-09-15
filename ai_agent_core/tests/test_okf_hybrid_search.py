# -*- coding: utf-8 -*-
"""Tester för hybridsökningen (fas 12, D9).

VARFÖR: `_okf_search` hade en tredelad fallback-kedja där varje steg var
trasigt på sitt eget sätt:

1. **"2b. tsvector-hybrid (swedish FTS)" var en lögn i en kommentar.**
   Koden var `summary ILIKE '%<hela prompten>%'`. En hel prompt matchar
   aldrig en konceptsammanfattning → grenen föll alltid igenom.
2. **pgvector-grenen dedupade inte.** `return self.browse(rows)` gav råa
   rad-id:n. Verifierat i drift: 110 rader, 4 unika koncept.
3. **create_date desc var sista utvägen.** En fråga utan träff
   returnerade de senaste koncepten som om de vore relevanta.

Testerna prövar beteendet som gör sökningen ärlig: dedup FÖRE limit,
viktad sammanslagning av två signaler, och tomt när det är tomt.
"""

from odoo.tests import common, tagged


@tagged('okf', 'search', 'post_install', '-at_install')
class TestOkfHybridSearch(common.TransactionCase):
    """12.1–12.8: en fråga, två signaler, ärlig tomhet."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Concept = cls.env['ai.okf.concept']
        cls.Atype = cls.env['ai.artifact.type']
        cls.atype_id = cls.Atype.search([], limit=1).id or \
            cls.env.ref('ai_agent_core.artifact_type_learning').id

    def _mk(self, key, summary, scope='company', version=1, atype=None):
        """Skapa ett koncept. Direkt via SQL för att styra version/status —
        `_okf_upsert` skulle lägga på versionslogik vi vill testa runt."""
        self.env.cr.execute("""
            INSERT INTO ai_okf_concept
                (concept_key, summary, scope, version, status, archived,
                 artifact_type_id, create_date, write_date)
            VALUES (%s, %s, %s, %s, 'stable', false, %s, now(), now())
            RETURNING id
        """, (key, summary, scope, version,
                atype.id if atype else self.atype_id))
        return self.env.cr.fetchone()[0]

    # ── 12.6: dedup ──

    def test_dedup_returns_one_row_per_concept(self):
        """KÄRNBUGGEN: 110 rader returnerades där 4 unika koncept fanns."""
        for v in range(1, 12):
            self._mk('dedup.test', 'Kunden vill ha fakturor via e-post.',
                     version=v)

        results = self.Concept._okf_search('fakturor', limit=20)
        keys = [c.concept_key for c in results]
        self.assertEqual(
            keys.count('dedup.test'), 1,
            '11 versioner av SAMMA koncept ska ge 1 träff, inte 11')

    def test_dedup_happens_before_limit(self):
        """Utan dedup FÖRE limit kunde limit=2 fyllas av 2 versioner av
        ett enda koncept — och resten av tabellen aldrig nås."""
        for v in range(1, 20):
            self._mk('limit.hog', 'Fakturor och fakturering.', version=v)
        self._mk('limit.annan', 'Fakturor skickas varje månad.', 1)

        results = self.Concept._okf_search('fakturor', limit=2)
        keys = {c.concept_key for c in results}
        self.assertEqual(len(keys), 2,
                         'båda koncepten ska nås, inte 2 versioner av ett')
        self.assertIn('limit.annan', keys)

    def test_dedup_picks_highest_version(self):
        """Senaste versionen ska vinna — inte en slumpmässig."""
        self._mk('ver.test', 'Gammal text om fakturor.', version=1)
        newest = self._mk('ver.test', 'Nyare text om fakturor.', version=7)

        results = self.Concept._okf_search('fakturor', limit=5)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].id, newest)

    # ── 12.5: ärlig tomhet ──

    def test_no_match_is_actually_empty(self):
        """DEN VIKTIGASTE: ingen träff får INTE bli 'senaste koncepten'."""
        self._mk('tomhet.test', 'Helt orelaterat innehåll om bananer.')

        results = self.Concept._okf_search('zzzznonsensezzz', limit=20)
        self.assertEqual(
            len(results), 0,
            'en fråga utan träff ska vara TOM — den gamla koden '
            'returnerade de senaste koncepten som om de vore svar')

    def test_scoped_search_honours_scope(self):
        self._mk('scope.a', 'Fakturor i företaget.', scope='company')
        self._mk('scope.b', 'Fakturor personligt.', scope='personal')

        company = self.Concept._okf_search('fakturor', scope='company')
        self.assertEqual({c.concept_key for c in company}, {'scope.a'})

    # ── 12.1/12.2: viktad sammanslagning ──

    def test_bm25_alone_carries_result_when_no_embedding(self):
        """12.3: i drift är ALLA embeddings NULL (Bifrost saknar modeller).
        Texten måste bära resultatet — annars är sökningen helt blind."""
        self._mk('bm25.test', 'Kunden vill ha fakturor via e-post.')

        results = self.Concept._okf_search('fakturor')
        self.assertEqual(len(results), 1,
                         'BM25 ska ensam bära resultatet utan vektorer')

    def test_semantic_weight_zero_is_bm25_only(self):
        self._mk('w0.test', 'Fakturor via e-post.')
        results = self.Concept._okf_search('fakturor', semantic_weight=0.0)
        self.assertEqual(len(results), 1)

    def test_weight_is_clamped(self):
        """En vikt utanför [0,1] är ett programmeringsfel, inte ett
        sätt att vikta bort en signal helt."""
        self._mk('clamp.test', 'Fakturor via e-post.')
        for w in (-1.0, 2.0, 99):
            results = self.Concept._okf_search('fakturor', semantic_weight=w)
            self.assertEqual(len(results), 1,
                             'vikt %s ska klämmas, inte krascha' % w)

    # ── 12.1: hybrid=False ──

    def test_hybrid_false_without_query_returns_latest(self):
        """Utan fråga är 'senaste' ett ärligt svar — det är inte en lögn."""
        self._mk('senaste.a', 'A.')
        self._mk('senaste.b', 'B.')
        results = self.Concept._okf_search('', hybrid=False, limit=5)
        self.assertGreaterEqual(len(results), 2)

    # ── 12.4: ILIKE-grenen är borta ──

    def test_long_prompt_does_not_silently_return_everything(self):
        """Den gamla '2b. tsvector-hybrid'-grenen var
        `summary ILIKE '%<hela prompten>%'`. Med en hel prompt matchade
        den aldrig — men hade den matchat hade den gett en träff per rad.
        Nu: ingen ILIKE, ingen falsk matchning."""
        self._mk('ilike.test', 'Kunden vill ha fakturor via e-post.')
        long_prompt = ('Du är en hjälpsam assistent som ska hjälpa till '
                       'med fakturor och annat administrativt arbete för '
                       'kunder i hela Sverige och Norden.')
        results = self.Concept._okf_search(long_prompt)
        self.assertEqual(
            len(results), 0,
            'en hel prompt ska inte ge träff via substrängmatchning')

    # ── 12.8: docstringen beskriver koden ──

    def test_docstring_admits_only_two_signals(self):
        doc = self.Concept._okf_search.__doc__ or ''
        self.assertIn('COALESCE', doc,
                      'docstringen ska nämna att text-signalen bär rader '
                      'utan vektor')
        self.assertIn('tomt', doc.lower(),
                      'docstringen ska nämna att tomt är tomt')
        self.assertIn('fallback', doc.lower(),
                      'docstringen ska nämna att fallback-kedjan är borta')
