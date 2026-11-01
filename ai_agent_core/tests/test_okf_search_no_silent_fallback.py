# -*- coding: utf-8 -*-
"""Sökningen får inte ge ett svar när det inte finns någon träff.

Buggen: `_okf_search` returnerade fem irrelevanta koncept för VARJE fråga
som saknade träff — inklusive en påhittad sträng. Mätt mot ledningssystem
2026-10-26: 12 frågor gav 6 unika id-sekvenser, och de sex sista
("zzz", "repo_apply", "WO-planering", "zxqwvbnmplkjhgf", "postgres",
"caddy", "dovecot") gav alla SAMMA fem rader — sex kalenderhändelser.

Två no-ops i `OR` gjorde hela tabellen till ett svar:

1. Kandidatfiltret `embedding IS NOT NULL OR search_vector @@ ...`.
   `embedding IS NOT NULL` är sant för varje rad med vektor. I samma
   stund som korpusen fick vektorer slutade villkoret filtrera något.
2. Grinden `cosine >= 0.39 OR ts_rank >= 1e-20`. Cosinus mäter NÄRHET,
   inte relevans: `embed-multilingual-v3.0` lägger nästan allt i samma
   trakt, och två obesläktade koncept låg på 0.82 mot varandra.

Det avgörande testet är INTE "nonsens ger tomt" — det kan passera av
misstag. Det är "två obesläktade frågor ger inte samma svar": det fångar
no-op-mekanismen utan att känna korpusens innehåll.
"""

from unittest.mock import patch

from odoo.tests import common, tagged


@tagged('okf', 'search', 'post_install', '-at_install')
class TestNoSilentNonEmptyFallback(common.TransactionCase):
    """En fråga utan relevans ska ge tomt — även med full vektortäckning."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Concept = cls.env['ai.okf.concept']
        cls.Atype = cls.env['ai.artifact.type']
        cls.atype_id = cls.Atype.search([], limit=1).id or \
            cls.env.ref('ai_agent_core.artifact_type_learning').id

    def _mk(self, key, summary, embedding=None, query=None):
        """Skapa ett company-koncept, ev. med vektor.

        `embedding` sätts via SQL: fältet är PgVector och en INSERT via
        ORM hade gått via cache-vägen. Vi vill styra kolumnen direkt.
        """
        self.env.cr.execute("""
            INSERT INTO ai_okf_concept
                (concept_key, summary, scope, status, archived,
                 artifact_type_id, owner_company_id, source_user_id,
                 create_date, write_date)
            VALUES (%s, %s, 'company', 'stable', false, %s, %s, NULL,
                    now(), now())
            RETURNING id
        """, (key, summary, self.atype_id, self.env.company.id))
        rec_id = self.env.cr.fetchone()[0]
        if query:
            self.env.cr.execute(
                "UPDATE ai_okf_concept SET summary = %s WHERE id = %s",
                (summary + ' ' + query, rec_id))
        if embedding is not None:
            self.env.cr.execute(
                "UPDATE ai_okf_concept SET embedding = %s::vector "
                "WHERE id = %s", (embedding, rec_id))
        return rec_id

    def _stub_provider(self, vector):
        """Provider som ger en frågevektor.

        Utan en frågevektor sätts `w = 0` och vektorgrenen är stängd — då
        kan buggen inte reproduceras, för då finns ingen vektor-signal att
        vara no-op på. Det var därför de första testversionerna passerade
        på den trasiga koden.
        """
        class _Stub:
            embedding_model = 'stub-model'
            name = 'stub'

            def _get_embedding(self, model=None, input=None,
                               input_type=None):
                return vector

        return _Stub()

    @staticmethod
    def _vec(seed, near=True):
        """En deterministisk 1024-dim vektor ur ett frö.

        `near=True` lägger vektorn i SAMMA trakt som alla andra — det är
        vad `embed-multilingual-v3.0` gör i praktiken. Mätt i drift: två
        obesläktade koncept låg på cosinus 0.82 mot varandra, och
        kalenderhändelserna på 0.75–0.85.

        Det är avgörande för testet: med slumpade vektorer i [-1,1] blir
        cosinus låg, brusgolvet 0.39 filtrerar bort allt, och buggen kan
        inte reproduceras. De första testversionerna passerade på den
        trasiga koden av precis det skälet.
        """
        import random
        rnd = random.Random(seed)
        if near:
            vals = (0.5 + rnd.uniform(0, 0.2) for _ in range(1024))
        else:
            vals = (rnd.uniform(-1, 1) for _ in range(1024))
        return '[' + ','.join('%.6f' % v for v in vals) + ']'

    # ── KÄRNTESTET (design D4) ──

    def test_unrelated_queries_do_not_share_a_result_set(self):
        """Två obesläktade frågor får inte ge IDENTISKT resultat.

        Det här är testet som fångar no-op-mekanismen: om grinden inte
        filtrerar något svarar sökningen likadant oavsett fråga, och då är
        "frånvaro av träff" omöjlig att skilja från "svag träff".
        """
        # Korpus där ALLA rader har vektor — läget som saknades när
        # retrieval-fusion 12.5 skrevs (då saknade alla rader vektor, och
        # `OR @@` var den enda vägen in, vilket var ofarligt).
        for i in range(6):
            self._mk('noop.%d' % i,
                     'Kalendermöte nummer %d om veckoplanering.' % i,
                     embedding=self._vec(i))

        # Frågevektorn är NÄRA korpusens vektorer (samma trakt) — det är
        # precis vad embed-multilingual-v3.0 gör, och det är därför
        # cosinus-golvet inte kan skilja brus från träff.
        qvec = self._vec(999)
        with patch.object(
                type(self.env['ai.provider']), '_embedding_provider',
                lambda self_: self._stub_provider(qvec)):
            a = self.Concept._okf_search('zxqwvbnmplkjhgf', limit=20)
            b = self.Concept._okf_search('qqqqwwww', limit=20)

        self.assertEqual(
            [c.id for c in a], [c.id for c in b],
            'två påhittade frågor ska ge SAMMA (tomma) resultat — inte '
            'samma fyllda limit')
        self.assertEqual(
            len(a), 0,
            'en påhittad sträng ska ge noll träffar, inte de mest '
            'centrala vektorerna som ser ut som ett svar')

    def test_nonsense_gives_empty_with_full_vector_coverage(self):
        """Nonsensfråga mot en korpus där alla rader har vektor → [].

        Det var precis det läget som inte fanns när 12.5 skrevs.
        """
        for i in range(5):
            self._mk('full.%d' % i,
                     'Postgres replikering och failover nummer %d.' % i,
                     embedding=self._vec(100 + i))

        qvec = self._vec(998)
        with patch.object(
                type(self.env['ai.provider']), '_embedding_provider',
                lambda self_: self._stub_provider(qvec)):
            results = self.Concept._okf_search('zxqwvbnmplkjhgf', limit=20)

        self.assertEqual(
            len(results), 0,
            'alla rader har vektor, men ingen matchar frågan — då ska '
            'svaret vara tomt')

    def test_real_hit_still_works_with_full_vector_coverage(self):
        """Motprovet: en riktig textträff får inte tappas av fixen."""
        self._mk('real.hit', 'Caddy är en reverse proxy.',
                 embedding=self._vec(7), query='caddyproxyterm')
        for i in range(5):
            self._mk('brus.%d' % i, 'Kalendermöte nummer %d.' % i,
                     embedding=self._vec(200 + i))

        with patch.object(
                type(self.env['ai.provider']), '_embedding_provider',
                lambda self_: None):
            results = self.Concept._okf_search('caddyproxyterm', limit=20)

        self.assertIn(
            'real.hit', [c.concept_key for c in results],
            'en äkta BM25-träff ska bäras även när korpusen har vektorer')

    # ── ELLER-semantiken (design D2) ──

    def test_row_without_embedding_is_carried_by_bm25(self):
        """En rad UTAN vektor som matchar i search_vector ska returneras.

        Det är ELLER-semantiken: den ena signalen är frånvarande, inte
        underkänd. Tappas den återinförs blindheten som okf-recall botade.
        """
        self._mk('bm25.only', 'Dovecot hanterar IMAP.',
                 query='dovecotimapterm')

        with patch.object(
                type(self.env['ai.provider']), '_embedding_provider',
                lambda self_: None):
            results = self.Concept._okf_search('dovecotimapterm', limit=20)

        self.assertIn(
            'bm25.only', [c.concept_key for c in results],
            'en rad utan vektor måste kunna bäras av BM25 ensam')

    def test_vector_ranks_but_does_not_decide(self):
        """Vektorn får RANGORDNA, inte AVGÖRA.

        Det är fixens kärna. En rad som matchar frågan i text men ligger
        långt från frågevektorn ska komma med; en rad som ligger nära
        frågevektorn men inte matchar texten ska INTE komma med.

        Skälet är mätt: på en korpus i samma trakt ligger orelaterade par
        på cosinus 0.99, så närhet säger inget om relevans. Textmatchning
        är det enda binära kriteriet vi har.
        """
        near = self._vec(42)
        # Matchar frågan i text, men ligger långt bort i vektorrummet.
        self._mk('text.match', 'Caddyserver hanterar TLS.',
                 embedding=self._vec(7, near=False), query='caddyterm')
        # Ligger exakt på frågevektorn, men matchar inte texten.
        self._mk('vector.only', 'Kalendermöte om veckoplanering.',
                 embedding=near)

        with patch.object(
                type(self.env['ai.provider']), '_embedding_provider',
                lambda self_: self._stub_provider(near)):
            results = self.Concept._okf_search('caddyterm', limit=20)

        keys = [c.concept_key for c in results]
        self.assertIn('text.match', keys,
                      'en äkta textträff ska komma med även om vektorn '
                      'ligger långt bort')
        self.assertNotIn('vector.only', keys,
                         'en rad som bara ligger nära frågevektorn får '
                         'inte fylla resultatet — närhet är inte relevans')

    def test_semantic_carries_only_when_corpus_has_no_text(self):
        """Undantaget: en korpus utan text alls får bäras av vektorn.

        Annars vore semantisk sökning omöjlig på en korpus där innehållet
        är embeddat men inte textindexerat. Läget är smalt och uttryckligt:
        det gäller bara när INGEN rad har text att matcha mot.
        """
        same = self._vec(42)
        # Tom summary → search_vector blir tom → ingen text att matcha.
        self._mk('sem.only', '', embedding=same)

        with patch.object(
                type(self.env['ai.provider']), '_embedding_provider',
                lambda self_: self._stub_provider(same)):
            results = self.Concept._okf_search(
                'zxqwvbnmplkjhgf', semantic_weight=1.0, limit=20)

        self.assertIn(
            'sem.only', [c.concept_key for c in results],
            'på en korpus utan text ska vektorn bära resultatet')

    # ── limit-semantiken (design D3, retrieval-fusion) ──

    def test_limit_still_counts_readable_deduped_rows(self):
        """`limit` avser läsbara, dedupade rader — oförändrat."""
        for v in range(10):
            self._mk('lim.same', 'Fakturor version %d.' % v,
                     query='limitterm')
        self._mk('lim.other', 'Fakturor andra.', query='limitterm')

        with patch.object(
                type(self.env['ai.provider']), '_embedding_provider',
                lambda self_: None):
            results = self.Concept._okf_search('limitterm', limit=2)

        keys = [c.concept_key for c in results]
        self.assertLessEqual(len(keys), 2)
        self.assertEqual(
            keys.count('lim.same'), 1,
            '10 versioner av samma koncept ska ge 1 rad, inte fylla limit')
