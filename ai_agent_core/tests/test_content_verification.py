# -*- coding: utf-8 -*-
"""Tester för innehållslig verifiering (innehallsverifiering).

Verifierar:
  (2.1)  deterministisk täckningsmätning mot en känd källa
  (2.2)  jämförelse sker bara när avtalet begär det
  (2.3)  täckning och tröskel rapporteras i resultatet
  (2.4)  under tröskeln ⇒ fail i requirement_errors + fix-förslag
  (2.5)  korrekt sammanfattning över tröskeln passerar
  (3.1)  påstådd källa som finns i sammanhanget ger ingen avvikelse
  (3.2)  påstådd källa som saknas rapporteras och namnges
  (3.3)  avvikelsen blir ett fel, inte en varning
  (5.1–5.4) acceptansfall: session 18204
"""

from odoo.tests import common, tagged

from odoo.addons.ai_agent_core.core.verify import (
    VerifyResult, ValidationStatus,
    extract_essential_parts, measure_coverage,
    _verify_source_coverage, _extract_claimed_sources,
    _find_unbacked_claims, _is_placeholder_html,
)


# Källan från session 18204 (det tråden faktiskt innehöll).
SOURCE = (
    'Gamers Nexus testade LG:s TV-modeller. Videon är 2 h 15 min med '
    '3 860 segment. Mikrofonen lyssnar även offline och buffrar ljud. '
    'LG sade "we own the glass" om panelen. webosbrew-datan visar '
    'faultmanager i webOS 4.0+. Modellerna QM65C, UM5N, SQ1, BZ och '
    '65EW960H nämndes. Se https://webosbrew.org/ och '
    'https://example.com/lg-rce'
)

# Det ofullständiga dokumentet från session 18204 (id 82).
CONTENT_INCOMPLETE = (
    'Sammanfattning av tråden om smart-TV.\n'
    'Vi diskuterade integritet och mikrofoner i moderna TV-apparater.\n'
    'Slutsatsen var att man bör vara försiktig.\n'
    'Källa: TV-guiden 2025/26 (SweClockers), Smart TV (Wikipedia)\n'
)

# Ett fullständigt dokument som återger källans väsentliga delar.
CONTENT_COMPLETE = (
    'Gamers Nexus testade LG:s modeller i en video på 2 h 15 min med '
    '3 860 segment. Mikrofonen lyssnar även offline och buffrar ljud. '
    'LG sade "we own the glass". webosbrew-datan visar faultmanager i '
    'webOS 4.0+. Modellerna QM65C, UM5N, SQ1, BZ och 65EW960H nämndes. '
    'Se https://webosbrew.org/ och https://example.com/lg-rce'
)


@tagged('post_install', '-at_install')
class TestContentVerification(common.TransactionCase):
    """Innehållslig verifiering mot en källa."""

    # ── 2.1 deterministisk täckningsmätning ───────────────────────────

    def test_extract_finds_essential_parts(self):
        """2.1: mätningen hittar entiteter, modeller, citat och URL:er."""
        parts = extract_essential_parts(SOURCE)
        kinds = {k for k, _ in parts}
        self.assertIn('url', kinds, 'URL:er ska plockas ut')
        self.assertIn('quote', kinds, 'citat ska plockas ut')
        self.assertIn('model', kinds, 'modellnamn ska plockas ut')
        self.assertIn('entity', kinds, 'namngivna entiteter ska plockas ut')
        texts = [t for _, t in parts]
        self.assertIn('QM65C', texts)
        self.assertIn('we own the glass', texts)

    def test_extract_ignores_stopwords(self):
        """2.1: vanliga ord blir inte 'entiteter'."""
        parts = extract_essential_parts('Det är en TV som man kan se på.')
        texts = [t.lower() for _, t in parts]
        for stop in ('det', 'en', 'som', 'man'):
            self.assertNotIn(stop, texts,
                             f'{stop!r} är ett stoppord och ska inte bli en entitet')

    def test_measure_coverage_low_for_incomplete(self):
        """2.1/5.1: ofullständigt innehåll ger låg täckning."""
        cov = measure_coverage(SOURCE, CONTENT_INCOMPLETE, threshold=0.6)
        self.assertLess(cov.coverage, 0.6)
        self.assertGreater(cov.total, 0)
        self.assertTrue(cov.missing, 'det som saknas ska listas')

    def test_measure_coverage_high_for_complete(self):
        """2.1/5.4: fullständigt innehåll ger hög täckning."""
        cov = measure_coverage(SOURCE, CONTENT_COMPLETE, threshold=0.6)
        self.assertGreaterEqual(cov.coverage, 0.6)
        self.assertTrue(cov.passed)

    # ── 2.3/2.4 täckning rapporteras och underkänns ───────────────────

    def test_coverage_reported_in_result(self):
        """2.3: täckning och tröskel framgår av resultatet."""
        r = VerifyResult()
        _verify_source_coverage(
            r, {'field': 'content', 'source': True, 'coverage': 0.6},
            'content', CONTENT_INCOMPLETE, SOURCE)
        self.assertIsNotNone(r.coverage, 'täckningen ska rapporteras')
        self.assertEqual(r.coverage_threshold, 0.6)
        self.assertGreater(r.coverage_total, 0)

    def test_below_threshold_is_requirement_error(self):
        """2.4/3.3: avvikelsen blir ett fel, inte en varning."""
        r = VerifyResult()
        _verify_source_coverage(
            r, {'field': 'content', 'source': True, 'coverage': 0.6},
            'content', CONTENT_INCOMPLETE, SOURCE)
        self.assertTrue(r.requirement_errors,
                        'avvikelsen ska ligga i requirement_errors')
        self.assertFalse(r.warnings,
                         'avvikelsen får INTE hamna i warnings')
        self.assertTrue(r.fix_suggestions,
                        'ett fix-förslag ska beskriva vad som saknas')

    def test_below_threshold_names_what_is_missing(self):
        """2.4/5.2: felet namnger vad som saknas."""
        r = VerifyResult()
        _verify_source_coverage(
            r, {'field': 'content', 'source': True, 'coverage': 0.6},
            'content', CONTENT_INCOMPLETE, SOURCE)
        msgs = ' '.join(e.message for e in r.requirement_errors)
        # Minst ett av de utelämnade beläggen ska namnges.
        named = any(tok in msgs for tok in
                    ('QM65C', 'we own the glass', 'webosbrew'))
        self.assertTrue(named, f'inget saknat belägg namngavs: {msgs[:200]}')

    def test_above_threshold_passes(self):
        """2.5/5.4: korrekt sammanfattning passerar (ingen överkänslighet)."""
        r = VerifyResult()
        _verify_source_coverage(
            r, {'field': 'content', 'source': True, 'coverage': 0.6},
            'content', CONTENT_COMPLETE, SOURCE)
        self.assertFalse(r.requirement_errors,
                         'en fullständig sammanfattning ska passera')

    def test_threshold_is_configurable(self):
        """2.5/D3: en lägre tröskel godkänner en kortare sammanfattning."""
        short = ('Gamers Nexus testade LG:s modeller och mikrofonen lyssnar '
                 'även offline. Modellerna QM65C och UM5N nämndes.')
        strict = VerifyResult()
        _verify_source_coverage(
            strict, {'field': 'content', 'source': True, 'coverage': 0.9},
            'content', short, SOURCE)
        lenient = VerifyResult()
        _verify_source_coverage(
            lenient, {'field': 'content', 'source': True, 'coverage': 0.2},
            'content', short, SOURCE)
        self.assertTrue(strict.requirement_errors,
                        'hög tröskel ska underkänna en kort sammanfattning')
        self.assertFalse(lenient.requirement_errors,
                         'låg tröskel ska godkänna samma sammanfattning')

    # ── 3.1–3.3 påstådd källa ─────────────────────────────────────────

    def test_extract_claimed_sources(self):
        """3.1: påstådda källor plockas ur innehållet."""
        claims = _extract_claimed_sources(CONTENT_INCOMPLETE)
        joined = ' '.join(claims)
        self.assertIn('SweClockers', joined)
        self.assertIn('Wikipedia', joined)

    def test_claimed_source_present_is_backed(self):
        """3.1: en källa som finns i sammanhanget ger ingen avvikelse."""
        content = 'Sammanfattning.\nKälla: webosbrew.org'
        claims = _extract_claimed_sources(content)
        unbacked = _find_unbacked_claims(claims, SOURCE)
        self.assertFalse(unbacked,
                         f'webosbrew finns i källan: {unbacked}')

    def test_claimed_source_absent_is_reported(self):
        """3.2/5.3: en påhittad källa rapporteras och namnges."""
        r = VerifyResult()
        _verify_source_coverage(
            r, {'field': 'content', 'source': True, 'coverage': 0.0},
            'content', CONTENT_INCOMPLETE, SOURCE)
        msgs = ' '.join(e.message for e in r.requirement_errors)
        self.assertIn('SweClockers', msgs,
                      'den påhittade källan ska namnges i felet')

    def test_claimed_source_absent_is_error_not_warning(self):
        """3.3: källavvikelsen blir ett fel."""
        r = VerifyResult()
        _verify_source_coverage(
            r, {'field': 'content', 'source': True, 'coverage': 0.0},
            'content', CONTENT_INCOMPLETE, SOURCE)
        self.assertTrue(r.requirement_errors)
        self.assertFalse(r.warnings)

    # ── 2.2/D1 ingen källa ⇒ ingen jämförelse ─────────────────────────

    def test_no_source_requested_no_comparison(self):
        """2.2/D1: utan source-villkor sker ingen innehållsjämförelse."""
        r = VerifyResult()
        # En vanlig non_empty-check ska inte röra täckningen.
        self.assertIsNone(r.coverage,
                          'coverage ska vara None när ingen källa begärs')

    def test_missing_source_is_an_error(self):
        """2.2: avtal som begär källa men ingen når fram ⇒ fel, inte tyst pass."""
        r = VerifyResult()
        _verify_source_coverage(
            r, {'field': 'content', 'source': True, 'coverage': 0.6},
            'content', CONTENT_COMPLETE, None)
        self.assertTrue(r.requirement_errors,
                        'utebliven källa ska rapporteras som fel')

    # ── 4.1/4.2 källan nås från körningsvägen ─────────────────────────

    def test_contract_needs_source(self):
        """4.1/4.2: bara avtal med source-villkor begär källan."""
        Coworker = self.env['ai.coworker']
        self.assertTrue(Coworker._contract_needs_source(
            {'checks': [{'field': 'c', 'source': True}]}))
        self.assertFalse(Coworker._contract_needs_source(
            {'checks': [{'field': 'c', 'non_empty': True}]}))
        self.assertFalse(Coworker._contract_needs_source({'checks': []}))
        self.assertFalse(Coworker._contract_needs_source({}))

    def test_build_verify_source_returns_none_without_session(self):
        """4.1: utan session blir källan None (ingen krasch)."""
        Coworker = self.env['ai.coworker']
        self.assertIsNone(Coworker._build_verify_source(None))


@tagged('post_install', '-at_install')
class TestNoiseFiltering(common.TransactionCase):
    """Brusfiltrering: mätningen ska mäta belägg, inte sökspår.

    Utan filtret dominerade sökmotor-omdirigeringar och katalogdomäner
    mätningen — i session 18204 var 8 av 12 URL:er Bing-omdirigeringar med
    400-teckens tokens, och 119 "citat" var mestadels fragment.
    """

    def test_search_engine_redirects_are_noise(self):
        """Sökmotor-omdirigeringar mäts inte som belägg."""
        src = ('Se https://www.bing.com/ck/a?!&&p=abc123&u=a1aHR0cHM6Ly9leGFtcGxl '
               'och https://example.com/riktig')
        parts = extract_essential_parts(src)
        urls = [t for k, t in parts if k == 'url']
        self.assertNotIn('bing.com', ' '.join(urls).lower(),
                         'Bing-omdirigering ska filtreras bort')
        self.assertTrue(any('example.com' in u for u in urls),
                        'en riktig URL ska finnas kvar')

    def test_catalog_domains_are_noise(self):
        """Katalogdomäner utan beläggvärde filtreras bort."""
        src = 'Se https://www.tv.nu/ och https://www.allatvkanaler.se/'
        urls = [t for k, t in extract_essential_parts(src) if k == 'url']
        self.assertFalse(urls, f'katalogdomäner ska filtreras: {urls}')

    def test_short_quotes_are_noise(self):
        """Korta fragment är inte citat."""
        src = 'Han sade "ok" om saken. Senare: "we own the glass" om panelen.'
        quotes = [t for k, t in extract_essential_parts(src) if k == 'quote']
        self.assertIn('we own the glass', quotes)
        self.assertNotIn('ok', quotes)

    def test_advertising_words_are_noise(self):
        """Annons-/sponsringsord är inte belägg."""
        src = 'Sponsored innehåll. Sponsrade länkar. Se även QM65C-modellen.'
        ents = [t.lower() for k, t in extract_essential_parts(src)
                if k == 'entity']
        self.assertNotIn('sponsored', ents)
        self.assertNotIn('sponsrade', ents)

    def test_model_names_survive_quota(self):
        """Kvoteringen låter modellnamn överleva en entitetstät källa.

        Utan kvoter fyllde 250 entiteter hela utrymmet och trängde ut
        modellnamnen — de mest värdefulla beläggen.
        """
        src = ('QM65C UM5N SQ1 BZ 65EW960H ' + ' '.join(
            f'Ord{i}' for i in range(400)))
        models = [t for k, t in extract_essential_parts(src) if k == 'model']
        for m in ('QM65C', 'UM5N', 'SQ1', '65EW960H'):
            self.assertIn(m, models,
                          f'{m} trängdes ut av entiteterna')

    def test_model_names_have_no_trailing_punctuation(self):
        """Modellnamn ska inte ha bindestreck/punkt i kanten."""
        src = 'Modellerna UR640- och 65EW960H- samt QM65C listades.'
        models = [t for k, t in extract_essential_parts(src) if k == 'model']
        for m in models:
            self.assertFalse(m.endswith(('-', '.')),
                             f'{m!r} har skiljetecken i kanten')

    def test_measurement_is_deterministic(self):
        """Samma källa ger samma delar — mätningen är förutsägbar (D2)."""
        a = extract_essential_parts(SOURCE)
        b = extract_essential_parts(SOURCE)
        self.assertEqual(a, b)


@tagged('post_install', '-at_install')
class TestPlaceholderHtml(common.TransactionCase):
    """HTML-platshållare ska räknas som tomt (fynd 1, session 18204).

    Odoo:s html-widget skriver ``<p><br></p>`` när ett fält saknar innehåll.
    Det är 11 tecken och passerade en naiv tomhetskontroll — vilket är hur
    document.page id 82 godkändes med ett tomt dokument.
    """

    def test_html_widget_default_is_placeholder(self):
        """Odoo:s standardvärde för tom html är en platshållare."""
        self.assertTrue(_is_placeholder_html('<p><br></p>'))

    def test_whitespace_and_nbsp_are_placeholder(self):
        """Blanksteg och &nbsp; är tomt innehåll."""
        for v in ('', '   ', '\n\t', '<p>&nbsp;</p>', '<div><br/></div>'):
            self.assertTrue(_is_placeholder_html(v), f'{v!r} ska vara tomt')

    def test_real_content_is_not_placeholder(self):
        """Riktigt innehåll är inte en platshållare."""
        for v in ('<h1>Reklamfri TV</h1>', '<p>QM65C är en modell</p>',
                  'text utan taggar'):
            self.assertFalse(_is_placeholder_html(v),
                             f'{v!r} är riktigt innehåll')

    def test_non_string_is_not_placeholder(self):
        """Icke-strängar hanteras utan krasch."""
        for v in (None, False, 0, []):
            self.assertFalse(_is_placeholder_html(v))
