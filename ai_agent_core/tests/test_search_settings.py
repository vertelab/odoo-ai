# -*- coding: utf-8 -*-
"""Fas 13 — sökinställningar och multi-source (okf-recall-path D7/D12).

Testerna prövar de tre saker som lätt blir halvbyggen:

1. **Strategin mappar till de tal den utger sig för.** En vikt som inte
   används är en kommentar, inte en inställning.
2. **Dedup sker över KÄLLOR.** Samma koncept via två backends ska bli en rad.
3. **En felande källa isoleras men syns.** `except: pass` är förbjudet —
   felet ska bli en `warning`, och övriga källor ska köra klart.

Dessutom: `graph_enrichment=False` hoppar över, och grafberikning mot den
TOMMA grafen (`odoo_mind` har 0 noder i drift) returnerar tomt utan att
krascha — den ska degradera, inte ljuga.
"""

import logging

from odoo.tests.common import TransactionCase, tagged

_logger = logging.getLogger(__name__)


@tagged('post_install', '-at_install', 'okf_search_settings')
class TestSearchSettings(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Coworker = cls.env['ai.coworker']
        cls.Source = cls.env['ai.search.source']
        cls.Concept = cls.env['ai.okf.concept']
        # En medarbetare att hänga inställningarna på.
        cls.coworker = cls.Coworker.create({
            'name': 'Söktest-Medarbetare (fas 13)',
            'is_default': True,
        })

    # ── 13.4: strategin mappar till tal ─────────────────────────────────

    def test_strategy_weights_are_monotonic(self):
        """Bredare strategi → lägre tröskel och fler rader.

        Om 'recall' gav färre rader än 'precision' vore namnen lögnaktiga.
        """
        p = self.Coworker._search_strategy_weights('precision')
        r = self.Coworker._search_strategy_weights('recall')
        self.assertGreater(p['min_score'], r['min_score'],
                           'precision ska ha högre tröskel än recall')
        self.assertLess(p['limit'], r['limit'],
                        'precision ska returnera färre rader än recall')
        self.assertGreaterEqual(p['semantic_weight'], r['semantic_weight'],
                                'precision ska lita mer på vektorn')

    def test_detective_is_broadest(self):
        d = self.Coworker._search_strategy_weights('detective')
        b = self.Coworker._search_strategy_weights('balanced')
        self.assertLess(d['min_score'], b['min_score'])
        self.assertGreaterEqual(d['limit'], b['limit'])

    def test_unknown_strategy_falls_back_to_balanced(self):
        """En okänd strategi får inte ge en tom dict (tyst KeyError)."""
        got = self.Coworker._search_strategy_weights('finns_inte')
        want = self.Coworker._search_strategy_weights('balanced')
        self.assertEqual(got, want)

    def test_every_strategy_has_all_keys(self):
        """Varje strategi ska bära HELA kontraktet — inklusive väg 2.

        Historik: testet hette `test_every_strategy_has_all_three_keys` och
        pinnade {min_score, semantic_weight, limit}. När väg 2 (§17.4) gav
        varje signal sin egen tröskel blev den enhetslösa summan fel mått
        att filtrera på, och trösklarna flyttade till min_cosine +
        min_ts_rank. Testet följde med i stället för att pinnas kvar vid
        en signatur som inte längre bär beslutet.

        `min_score` finns kvar som HÄRLETT värde (speglar min_cosine) för
        att den gamla anropsytan och loggningen inte ska gå sönder — men
        det är inte längre ett filter.
        """
        for s in ('precision', 'balanced', 'recall', 'detective'):
            w = self.Coworker._search_strategy_weights(s)
            self.assertEqual(
                set(w),
                {'min_score', 'min_cosine', 'min_ts_rank',
                 'semantic_weight', 'limit'},
                '%s saknar nycklar' % s)

    def test_thresholds_are_per_signal(self):
        """Väg 2: cosine- och BM25-trösklarna lever i olika enheter.

        Testar inte att talen är "rätt" (de kalibreras mot korpusen) utan
        att de är på RÄTT SKALA. Ett cosine-golv under 0.3 fångar brus, och
        en ts_rank-tröskel över ~0.1 är död (mätt max ~0.061). Det var
        precis den sortens omöjliga tal — 0.7 mot en skala som toppar på
        0.06 — som gjorde den gamla tabellen blind.
        """
        for s in ('precision', 'balanced', 'recall', 'detective'):
            w = self.Coworker._search_strategy_weights(s)
            self.assertGreaterEqual(
                w['min_cosine'], 0.35,
                '%s: cosine-golv under bruset (0.36–0.39 mätt)' % s)
            self.assertLessEqual(
                w['min_cosine'], 1.0, '%s: cosine > 1 är omöjligt' % s)
            self.assertLessEqual(
                w['min_ts_rank'], 0.10,
                '%s: ts_rank-tröskel över skalans tak (~0.061) → blind' % s)
            self.assertGreaterEqual(
                w['min_ts_rank'], 0.0,
                '%s: negativ ts_rank-tröskel är meningslös' % s)

    def test_min_score_mirrors_min_cosine(self):
        """Den härledda min_score ska spegla min_cosine, inte ljuga."""
        for s in ('precision', 'balanced', 'recall', 'detective'):
            w = self.Coworker._search_strategy_weights(s)
            self.assertEqual(w['min_score'], w['min_cosine'])

    # ── 13.2: källkatalogen är ärlig ────────────────────────────────────

    def test_seed_creates_all_sources(self):
        codes = set(self.Source.search([]).mapped('code'))
        self.assertTrue(
            {'okf_concept', 'ai_memory', 'graph', 'documents'} <= codes,
            'katalogen ska innehålla alla fyra källorna, fick: %s' % codes)

    def test_only_okf_is_wired(self):
        """Fas 13 kopplar in EN backend. De andra är katalogposter.

        Om `is_wired` vore True för en okopplad källa skulle den synas i
        search_sources utan att bidra med en rad — exakt mönstret från
        design.md §15.
        """
        wired = self.Source.search([('is_wired', '=', True)])
        self.assertEqual(wired.mapped('code'), ['okf_concept'],
                         'endast okf_concept får vara inkopplad i fas 13')

    def test_unwired_sources_explain_themselves(self):
        for s in self.Source.search([('is_wired', '=', False)]):
            self.assertTrue(
                s.wired_note,
                '%s är okopplad men förklarar inte varför' % s.code)

    def test_backend_keys_are_known(self):
        """Katalogen får inte peka på en backend ingen kan dispatca."""
        known = self.Source._known_backends()
        for s in self.Source.search([]):
            self.assertIn(s.backend, known)

    def test_create_rejects_unknown_backend(self):
        from odoo.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            self.Source.create({
                'name': 'Påhittad', 'code': 'fake.test',
                'backend': 'ingen_sadan_backend'})

    def test_seed_is_idempotent(self):
        before = self.Source.search_count([])
        self.Source._seed_sources()
        self.Source._seed_sources()
        self.assertEqual(self.Source.search_count([]), before,
                         'seedning ska vara idempotent')

    # ── 13.3: profil-seedning ───────────────────────────────────────────

    def test_session_only_has_no_sources_and_no_hybrid(self):
        vals = self.Coworker._search_profile_presets('session_only')
        self.assertFalse(vals['hybrid_search'],
                         'session_only ska inte hybridsöka i ett minne')
        self.assertFalse(vals['graph_enrichment'])
        self.assertEqual(vals['search_sources'], [(5, 0, 0)],
                         'session_only ska rensa källorna')

    def test_hermes_is_the_broadest_profile(self):
        h = self.Coworker._search_profile_presets('hermes')
        b = self.Coworker._search_profile_presets('balanced')
        self.assertEqual(h['search_strategy'], 'detective')
        self.assertGreaterEqual(h['graph_enrichment_hops'],
                                b['graph_enrichment_hops'])

    def test_create_seeds_sources_from_profile(self):
        c = self.Coworker.create({
            'name': 'Seedtest (fas 13)', 'is_default': True,
            'memory_profile': 'hermes'})
        self.assertTrue(c.search_sources,
                        'hermes ska seedas med källor vid skapande')
        self.assertEqual(c.search_strategy, 'detective')

    def test_write_profile_reseeds(self):
        c = self.Coworker.create({
            'name': 'Bytestest (fas 13)', 'is_default': True,
            'memory_profile': 'balanced'})
        c.write({'memory_profile': 'session_only'})
        self.assertFalse(c.search_sources,
                         'byte till session_only ska tömma källorna')
        self.assertFalse(c.hybrid_search)

    # ── 13.5/13.6: multi-source recall ──────────────────────────────────

    def test_no_sources_is_honestly_empty(self):
        c = self.Coworker.create({
            'name': 'Tomtest (fas 13)', 'is_default': True,
            'memory_profile': 'session_only'})
        recs, diag = c._multi_source_recall('något', scope='company',
                                            owner_id=1)
        self.assertFalse(recs)
        self.assertIn('empty_reason', diag)

    def test_failing_source_is_isolated_and_warned(self):
        """En backend som kastar får inte sänka de andra, och inte tigas.

        Vi monterar in en trasig backend i dispatch-tabellen och kräver att
        anropet returnerar (inte kastar) samt att källan blir tom — men att
        den ÄR kvar i per_source, så att felet går att se.
        """
        from unittest.mock import patch

        c = self.coworker
        src = self.Source.search([('code', '=', 'okf_concept')], limit=1)
        self.assertTrue(src.is_wired)

        CoworkerModel = type(c)
        original = CoworkerModel._search_backends

        def _broken(self, *a, **kw):
            table = original(self, *a, **kw)

            def _boom(*a2, **kw2):
                raise RuntimeError('avsiktligt backend-fel (test)')
            table['okf_concept'] = _boom
            return table

        # patch.object återställer EXAKT det som fanns — att binda om på
        # klassen lämnar kvar ett attribut som Odoos testramverk flaggar.
        with patch.object(CoworkerModel, '_search_backends', _broken):
            with self.assertLogs(
                    'odoo.addons.ai_agent_core.models.ai_coworker',
                    level='WARNING') as cm:
                recs, diag = c._multi_source_recall(
                    'roller', scope='personal',
                    owner_id=self.env.user.id)
            self.assertFalse(recs, 'en trasig källa ska ge tomt, inte krasch')
            self.assertIn('okf_concept', diag['per_source'],
                          'den felande källan ska synas i diagnostiken')
            self.assertTrue(
                any('misslyckades' in m for m in cm.output),
                'felet ska loggas som warning, inte sväljas')

    def test_unwired_source_is_skipped_without_error(self):
        """En katalogpost utan inkoppling får inte kasta."""
        c = self.coworker
        graph_src = self.Source.search([('code', '=', 'graph')], limit=1)
        hits = c._recall_from_source(
            graph_src, 'x', 'company', 1,
            {'limit': 10, 'min_score': 0.0, 'semantic_weight': 0.7})
        self.assertEqual(hits, [])

    def test_recall_returns_tuples_of_record_and_score(self):
        c = self.coworker
        recs, diag = c._multi_source_recall(
            'roller', scope='personal', owner_id=self.env.user.id)
        for r in recs:
            self.assertEqual(r._name, 'ai.okf.concept')
        self.assertIn('strategy', diag)
        self.assertIn('per_source', diag)

    # ── 13.8/13.9: grafberikning ────────────────────────────────────────

    def test_graph_enrich_on_empty_graph_returns_empty(self):
        """Den verkliga grafen (`odoo_mind`) har 0 noder i drift.

        Berikningen ska degradera till tom sträng — inte krascha, och inte
        hitta på innehåll.
        """
        c = self.coworker
        recs, _diag = c._multi_source_recall(
            'roller', scope='personal', owner_id=self.env.user.id)
        if not recs:
            self.skipTest('inga koncept i scopet att berika')
        text = c._graph_enrich(recs)
        self.assertEqual(text, '',
                         'en tom graf ska ge tom berikning')

    def test_graph_enrich_with_no_records_is_empty(self):
        empty = self.env['ai.okf.concept'].browse()
        self.assertEqual(self.coworker._graph_enrich(empty), '')

    def test_graph_enrichment_disabled_writes_no_graph_block(self):
        """`graph_enrichment=False` ska ge en injektion utan GRAF-KONTEXT."""
        c = self.Coworker.create({
            'name': 'Grafav (fas 13)', 'is_default': True,
            'memory_profile': 'balanced'})
        c.graph_enrichment = False
        text = c._build_injection_prompt(prompt='roller')
        self.assertNotIn('GRAF-KONTEXT', text)

    # ── §17: tröskeln som filtrerar bort allt ─────────────────────────
    def _mk_concept(self, key, summary, scope='company'):
        """Skapa ett koncept via SQL (samma mönster som fas 12:s _mk)."""
        atype = self.env['ai.artifact.type'].search([], limit=1)
        self.env.cr.execute("""
            INSERT INTO ai_okf_concept
                (concept_key, summary, scope, version, status, archived,
                 artifact_type_id, create_date, write_date)
            VALUES (%s, %s, %s, 1, 'stable', false, %s, now(), now())
            RETURNING id
        """, (key, summary, scope, atype.id))
        return self.env.cr.fetchone()[0]

    def test_thresholds_do_not_make_real_hits_disappear(self):
        """Väg 2: trösklarna får inte filtrera bort en VERKLIG träff.

        Historik: testet hette `test_min_score_is_not_wired_into_multi_
        source_recall` och pinnade en medveten icke-koppling. Med väg 2
        (§17.4) är trösklarna inkopplade — men per signal, i rätt enhet.
        Det som fortfarande måste hålla är det som var poängen hela tiden:
        en faktisk träff ska inte försvinna.

        Därför testas BETEENDET, inte frånvaron av ett filter. Det gamla
        testet kunde passera fastän sökningen var blind — det räckte att
        filtret var borta. Det här testet fallerar om trösklarna blir
        för höga, vilket är den faktiska risken.
        """
        c = self.Coworker.create({
            'name': 'Trogav (fas 13)', 'is_default': True,
            'memory_profile': 'balanced'})
        self._mk_concept('trogav.test', 'kvantmekanik sällsynt ordet')

        # Konceptet SKA hittas. En BM25-träff ligger långt under de gamla
        # min_score-värdena men över min_ts_rank — det är skillnaden mellan
        # ett filter på fel skala och ett på rätt.
        recs, diag = c._multi_source_recall('kvantmekanik', scope='company')
        self.assertTrue(
            recs, 'en faktisk träff får inte filtreras bort av trösklarna')
        self.assertIn('trogav.test', recs.mapped('concept_key'))

        # Och trösklarna ska vara på RÄTT skala för sina signaler: BM25-
        # tröskeln under skalans tak (~0.061), cosine-golvet över bruset.
        for strat in ('precision', 'balanced', 'recall', 'detective'):
            w = c._search_strategy_weights(strat)
            self.assertLessEqual(
                w['min_ts_rank'], 0.10,
                'BM25-tröskeln ligger över skalans tak → varje BM25-träff '
                'filtreras bort (se design.md §17.4)')
            self.assertGreaterEqual(
                w['min_cosine'], 0.35,
                'cosine-golvet ligger under brusnivån (mätt 0.36–0.39)')

    def test_bm25_gate_uses_separate_scale(self):
        """En rad UTAN vektor ska kunna passera på sin BM25-signal.

        Det här är hela poängen med väg 2 och det som ALS inte fick tappas
        när trösklarna kopplades in: en rad utan embedding har cosine=NULL.
        Med ett OCH-villkor hade den aldrig kunnat passera. Med ELLER kan
        den det — och det är därför väg 2 valdes.
        """
        self._mk_concept('vag2.utanvektor', 'kvantmekanik sällsynt ordet')

        # Sätt en omöjlig cosine-tröskel men en rimlig BM25-tröskel: bara
        # en OR-semantik kan då släppa igenom den vektorlösa träffen.
        recs = self.Concept._okf_search(
            'kvantmekanik', limit=10,
            min_cosine=0.99, min_ts_rank=1e-20)
        self.assertTrue(
            recs,
            'en vektorlös rad med BM25-träff måste passera på sin egen '
            'signal — annars är trösklarna ett OCH och detective blir blind')

    def test_impossible_cosine_alone_is_honestly_empty(self):
        """Utan BM25-signal SKA en omöjlig cosine ge tomt — ärligt tomt."""
        recs = self.Concept._okf_search(
            'zzzznonsensezzz', limit=10,
            min_cosine=0.99, min_ts_rank=0.5)
        self.assertFalse(
            recs, 'omöjliga trösklar ska ge tomt, inte de senaste koncepten')

    def test_no_thresholds_means_no_strategy_gate(self):
        """Utan trösklar läggs ingen STRATEGI-gate på — bakåtkompatibilitet.

        `_tool_okf_search` och fas 12:s tester anropar utan trösklar. En
        kalibrerad strategitröskel hade tyst ändrat deras beteende.

        OBS (§19): det betyder INTE "ingen grind alls". Sedan väg 2 finns
        ett BRUSGOLV (`min_cosine=0.39`) på den ogrindade vägen också —
        annars returnerade den allt, eftersom `embedding IS NOT NULL`
        släpper in hela tabellen så snart raderna har vektorer. Ett äkta
        BM25-möte passerar ändå, eftersom `min_ts_rank` sätts samtidigt.
        """
        atype = self.env['ai.artifact.type'].search([], limit=1)
        self.env.cr.execute("""
            INSERT INTO ai_okf_concept
                (concept_key, summary, scope, version, status, archived,
                 artifact_type_id, create_date, write_date)
            VALUES ('vag2.ingen-gate', 'kvantmekanik sällsynt ordet',
                    'company', 1, 'stable', false, %s, now(), now())
            RETURNING id
        """, (atype.id,))
        truth = self.Concept._okf_search('kvantmekanik', limit=10)
        self.assertTrue(
            truth,
            'ett äkta BM25-möte ska överleva även den ogrindade vägen — '
            'brusgolvet får inte klippa bort rader utan vektor (§19.5)')

    def test_noise_is_empty_even_without_thresholds(self):
        """§19: den ogrindade vägen får inte heller returnera allt.

        Detta var regressionen: `embedding IS NOT NULL OR @@` släppte in
        hela tabellen när raderna fick vektorer, och utan grind kom 4 rader
        tillbaka för en nonsens-fråga. Ett positivt cosine-tal är inte ett
        relevansbevis — brusgolvet måste finnas även här.
        """
        atype = self.env['ai.artifact.type'].search([], limit=1)
        self.env.cr.execute("""
            INSERT INTO ai_okf_concept
                (concept_key, summary, scope, version, status, archived,
                 artifact_type_id, create_date, write_date)
            VALUES ('vag2.brus', 'Kunden vill ha fakturor via e-post.',
                    'company', 1, 'stable', false, %s, now(), now())
            RETURNING id
        """, (atype.id,))
        hits = self.Concept._okf_search('zzzznonsensezzz', limit=20)
        self.assertFalse(
            hits,
            'en fråga utan träff ska vara TOM även utan trösklar — '
            'annars är "tomt" bara sant så länge ingen rad har vektor')
