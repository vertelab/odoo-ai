# -*- coding: utf-8 -*-
"""Grind: en testkörning får inte tillföra fel som inte står i baslinjen.

Ändring: test-suite-baseline (uppgift 2.1 och 2.2).

VARFÖR: ``BASELINE.md`` skrev ned svitens kända fel — men en nedskriven
tabell som ingen läser är en påminnelse, inte en grind. Den här filen gör
baslinjen prövbar: den läser tabellen, läser en körd sviten-logg och
jämför.

Grinden fallerar på fyra saker:

1. **Ett nytt fel.** En klass i loggen som inte står i baslinjen.
2. **En post utan skäl.** En rad i baslinjetabellen vars orsakskolumn är
   tom — "att lägga till ett fel utan skäl är att sänka ribban i smyg".
3. **En post utan stabilitet.** En rad som varken är `stabil` eller
   `vandrande` kan grinden inte tolka (test-baseline-determinism D4).
4. **En post som inte längre fallerar.** Baslinjen ska krympa när ett fel
   åtgärdas; en kvarvarande rad döljer nästa överträdelse.

Ett **vandrande** fel fallerar i några körningar men inte alla
(ordningsberoende i testfixturerna). Det **rapporteras** som varning men
fäller inte grinden — en slumpmässigt röd grind slutar någon läsa, och det
är värre än ingen grind (test-baseline-determinism 1.1-1.4). Ett
vandrande fel som fallerar i **alla** lästa loggar har blivit stabilt och
fäller med ett meddelande om att posten ska flyttas.

Felen i BEROENDE moduler (``setUpClass`` i ``web``, ``web_editor``,
``web_pwa_*``) räknas inte in — de tillhör sina moduler och står under
rubriken "utanför `ai_agent_core`" i ``BASELINE.md``.

Körning::

    # En logg (bakåtkompatibelt):
    AI_AGENT_CORE_SUITE_LOG=/tmp/suite.log \
        odoo --test-tags=/ai_agent_core:TestSuiteBaseline

    # Flera loggar — så avgörs stabilitet (rekommenderat):
    AI_AGENT_CORE_SUITE_LOGS=/tmp/run1.log,/tmp/run2.log,/tmp/run3.log \
        odoo --test-tags=/ai_agent_core:TestSuiteBaseline

Utan ``$AI_AGENT_CORE_SUITE_LOGS`` läses en logg från
``$AI_AGENT_CORE_SUITE_LOG`` eller en kandidat i ``/tmp``. Med bara en
logg kan stabilitet inte avgöras: en klass som fallerar behandlas då som
``stabil`` (det går inte att säga närmare).

Utan en logg att läsa hoppar jämförelsen över sig själv (den kan inte
mäta något), men tabellens innehåll prövas alltid.
"""

import logging
import os
import pathlib
import re
import unittest

from odoo.tests import TransactionCase, tagged


_logger = logging.getLogger(__name__)


MODULE_ROOT = pathlib.Path(__file__).resolve().parent.parent
BASELINE_PATH = MODULE_ROOT / 'tests' / 'BASELINE.md'

MODULE_NAME = 'ai_agent_core'

#: Miljövariabel som pekar ut EN logg. Bakåtkompatibel enkel-logg-fallback.
SUITE_LOG_ENV = 'AI_AGENT_CORE_SUITE_LOG'

#: Miljövariabel som pekar ut FLERA loggar, kommaseparerade
#: (test-baseline-determinism D1). Med flera loggar kan stabilitet avgöras:
#: en klass som fallerar i alla är `stabil`, i några är `vandrande`.
SUITE_LOGS_ENV = 'AI_AGENT_CORE_SUITE_LOGS'

#: De två giltiga stabilitetsvärdena (BASELINE.md, kolumnen Stabilitet).
STABLE = 'stabil'
FLAKY = 'vandrande'
VALID_STABILITY = (STABLE, FLAKY)

#: Letas i tur och ordning när miljövariabeln saknas. Första fil som
#: innehåller en svit-sammanfattning används.
SUITE_LOG_CANDIDATES = ('/tmp/suite.log', '/tmp/tst_suite.log')

#: Moduler vars ``setUpClass``-fel är kända och ligger utanför baslinjen.
#: Prefixmatchning: ``web`` täcker ``web_editor``, ``web_pwa_*``,
#: ``web_unsplash`` osv.
FOREIGN_MODULE_PREFIXES = ('web', 'base', 'mail', 'portal', 'bus')

#: Rubriken i BASELINE.md som skiljer ut fel utanför modulen. Alla
#: ``|``-tabeller EFTER den rubriken hör till de främmande modulerna.
FOREIGN_SECTION = 'utanför'

#: ``Module ai_agent_core: 0 failures, 0 errors`` — svitens egen rad.
MODULE_SUMMARY_RE = re.compile(
    r'Module\s+(?P<module>[\w.]+):\s+(?P<failures>\d+)\s+failures?,\s+'
    r'(?P<errors>\d+)\s+errors?')

#: En felrad i Odoo-loggen. Två former förekommer:
#:   ``2026-11-02 15:29:37,369 4964 ERROR tst_c odoo.addons.…: FAIL: TestX.test_y``
#:   ``2026-11-02 15:29:37,369 4964 ERROR tst_c odoo.addons.…: ERROR: TestX.setUpClass``
#: Addon-namnet är det som avgör om felet hör till oss.
TEST_FAILURE_RE = re.compile(
    r'\b(?P<level>FAIL|ERROR):\s+(?P<test>[\w.]+)')
ADDON_RE = re.compile(r'odoo\.addons\.(?P<addon>\w+)')


def _read_baseline(path=None):
    """Läs baslinjens tabeller. Returnerar ``(kända, främmande)``.

    ``kända``    — ``[(klass, antal, orsak, stabilitet)]`` för
    ``ai_agent_core``.
    ``främmande`` — samma form, men för moduler utanför baslinjen.

    Tabellerna skiljs av rubriken ``utanför``: allt före den hör till
    modulen, allt efter till beroendena. En rubrikrad i markdown börjar
    med ``#``; kolumnrubriken (``| Kategori | …``) hoppas över eftersom
    dess första cell inte går att göra om till ett antal.

    Stabilitetskolumnen ligger SIST (D2) eftersom ``_parse_count()`` läser
    cell 2 och orsaken är cell 3+. En post utan stabilitet ger ``None`` —
    grinden behandlar det som ett fel (D4).
    """
    # Sökvägen läses vid anropet, inte vid import: testerna i
    # `test_baseline_gate` pekar om `BASELINE_PATH` mot en tempfil, och ett
    # default-argument hade bundit den riktiga filen för alltid.
    path = path or BASELINE_PATH
    known, foreign = [], []
    target = known
    in_table = False
    for line in path.read_text(encoding='utf-8').splitlines():
        stripped = line.strip()
        if stripped.startswith('#') and FOREIGN_SECTION in stripped.lower():
            target = foreign
            in_table = False
            continue
        if not stripped.startswith('|'):
            # En tabell tar slut vid första icke-tabellraden. Utan detta
            # läses varje `|`-tabell i filen som baslinjeposter.
            in_table = False
            continue
        cells = [c.strip() for c in stripped.strip('|').split('|')]
        # Rubrikraden avgör om det här är en baslinjetabell. Tidigare
        # filtrerades rader på att kolumn 2 började med en siffra — men
        # tabellen har FELMEDDELANDET där (`3 != 1`), så varje rad
        # hoppades över och baslinjen lästes som tom. Grinden kunde därför
        # aldrig bli grön mot en riktig logg (upptäckt 2026-11-09 när
        # grinden fick sin första riktiga logg att läsa).
        if cells and cells[0].lstrip('`*') == 'Test':
            in_table = True
            continue
        if not in_table:
            continue
        if len(cells) < 3 or set(cells[1]) <= set('-: '):
            continue
        # Kolumn 2 är ``Fel`` — oftast ett felmeddelande (``3 != 1``),
        # inte ett antal. `_parse_count()` ger då None, vilket är rätt:
        # jämförelsen sker på KLASSNAMN, inte på antal. Raden ska ändå med.
        count = _parse_count(cells[1])
        # Stabiliteten är SISTA cellen (D2); orsaken är cell 3 fram till
        # dess. Utan stabilitetskolumn (äldre tabell) blir orsaken allt
        # från cell 3 och stabiliteten None — vilket grinden fäller på.
        has_stability = len(cells) > 3
        stability = _parse_stability(cells[-1]) if has_stability else None
        reason = ' '.join(cells[2:-1] if has_stability else cells[2:]).strip()
        target.append((_parse_class(cells[0]), count, reason, stability))
    return known, foreign


def _parse_count(cell):
    """Antal ur en cell: ``22``, ``125 (…)`` eller ``—`` → ``None``.

    ``—`` betyder "ingen siffra", inte noll — en rad utan antal kan inte
    jämföras mot loggen och ska därför inte räknas som en känd klass.
    """
    match = re.match(r'(\d+)', cell)
    return int(match.group(1)) if match else None


def _parse_class(cell):
    """Klassnamnet ur en tabellcell — normaliserat till loggens form.

    Två normaliseringar, båda nödvändiga för att en post ska matcha sin
    loggklass:

    1. **Markdown-formatering skalas bort.** Tabellen skriver
       ``` `TestX` ``` med backticks; loggen ger nakna namn.
    2. **Metoden kastas.** Tabellen namnger ofta hela testet
       (``TestX.test_y``) eller en grupp (``TestLineage (3 st)``), medan
       ``_parse_suite_log()`` ger klassnamnet — allt före första punkten
       (samma regel där).

    Utan det här matchar ingen post sin logg: varje känt fel rapporteras
    som nytt OCH som kvarvarande, och grinden faller på allt, alltid
    (upptäckt 2026-11-09 när grinden fick sin första riktiga logg att
    läsa).
    """
    text = (cell or '').strip().replace('`', '').strip()
    # ``TestLineage (3 st)`` → ``TestLineage``
    text = re.sub(r'\s*\(.*\)\s*$', '', text)
    # ``TestX.test_y`` → ``TestX``
    return text.split('.')[0].strip()


def _parse_stability(cell):
    """Stabilitet ur en cell: ``stabil``/``vandrande``, annars ``None``.

    ``None`` betyder "ingen stabilitet angiven" — grinden behandlar det som
    ett fel (D4): en post som inte går att tolka är värre än ingen post.
    Cellen normaliseras till gemener och markdown-kursiv (``*stabil*``)
    skalas bort, så en formaterad tabell inte tyst blir "utan stabilitet".
    """
    text = (cell or '').strip().strip('*_').strip().lower()
    return text if text in VALID_STABILITY else None


def _parse_suite_log(path):
    """Läs en körd sviten-logg.

    Returnerar ``(sammanfattning, egna_fel, främmande_fel)`` där

    ``sammanfattning`` — ``{'module': (failures, errors)}`` per modul,
    ``egna_fel``      — ``{klassnamn: antal}`` för ``ai_agent_core``,
    ``främmande_fel`` — samma form, för andra addons.
    """
    summary = {}
    own, foreign = {}, {}
    addon = None
    for line in path.read_text(encoding='utf-8', errors='replace').splitlines():
        match = MODULE_SUMMARY_RE.search(line)
        if match:
            summary[match.group('module')] = (
                int(match.group('failures')), int(match.group('errors')))
            continue
        match = TEST_FAILURE_RE.search(line)
        if not match:
            continue
        addon_match = ADDON_RE.search(line)
        addon = addon_match.group('addon') if addon_match else None
        # Klassnamnet är allt före första punkten; ``TestX.setUpClass`` och
        # ``TestX.test_y`` hör båda till ``TestX``.
        cls = match.group('test').split('.')[0]
        bucket = own if addon == MODULE_NAME else foreign
        bucket[cls] = bucket.get(cls, 0) + 1
    return summary, own, foreign


def _find_suite_logs():
    """Alla loggar att jämföra mot — en lista (D1).

    Prioritet:

    1. ``$AI_AGENT_CORE_SUITE_LOGS`` — kommaseparerad lista. Det är den
       enda vägen till att kunna avgöra stabilitet: med flera loggar blir
       "fallerar i alla" och "fallerar i några" två olika svar.
    2. ``$AI_AGENT_CORE_SUITE_LOG`` — en logg (bakåtkompatibelt).
    3. Kandidaterna i ``/tmp``.

    Icke-existerande sökvägar i listan hoppas över med en varning: en
    felstavad sökväg får inte tyst göra listan till en enda logg — då
    klassas allt som fallerar som ``stabil``, vilket är den farliga
    riktningen (en vandrande klass blir ett grindfel).
    """
    explicit = os.environ.get(SUITE_LOGS_ENV)
    if explicit:
        paths = []
        for raw in explicit.split(','):
            candidate = pathlib.Path(raw.strip())
            if not raw.strip():
                continue
            if candidate.is_file():
                paths.append(candidate)
            else:
                _logger.warning(
                    '%s: %s finns inte — hoppas över', SUITE_LOGS_ENV,
                    candidate)
        if paths:
            return paths
    single = _find_suite_log()
    return [single] if single else []


def _find_suite_log():
    """Sökvägen till loggen, eller ``None`` om ingen finns."""
    explicit = os.environ.get(SUITE_LOG_ENV)
    if explicit:
        path = pathlib.Path(explicit)
        return path if path.is_file() else None
    for candidate in SUITE_LOG_CANDIDATES:
        path = pathlib.Path(candidate)
        if path.is_file():
            return path
    # Sista utväg: en tst_*-logg som innehåller en svit-sammanfattning.
    for path in sorted(pathlib.Path('/tmp').glob('tst_*.log')):
        try:
            head = path.read_text(encoding='utf-8', errors='replace')
        except OSError:
            continue
        if MODULE_SUMMARY_RE.search(head):
            return path
    return None


def _failures_per_log(logs):
    """``[{klass: antal}, ...]`` — egna fel per logg (D1/3.3).

    En post per logg som innehåller en svit-sammanfattning för modulen.
    Loggar utan sammanfattning hoppas över: de är trunkerade eller från en
    annan körning, och att räkna dem som "inga fel" skulle göra varje klass
    vandrande.
    """
    per_log = []
    for log in logs:
        summary, own, _foreign = _parse_suite_log(log)
        if MODULE_NAME not in summary:
            _logger.warning(
                '%s innehåller ingen sammanfattning för %s — hoppas över',
                log, MODULE_NAME)
            continue
        per_log.append(own)
    return per_log


def _classify_stability(logs, own_failures_per_log=None):
    """``{klass: 'stabil'|'vandrande'}`` ur flera loggar (D1/3.3).

    * **stabil** — klassen fallerar i ALLA lästa loggar.
    * **vandrande** — klassen fallerar i NÅGRA men inte alla.

    Med bara EN logg kan stabilitet inte avgöras: då är varje klass som
    fallerar ``stabil`` (det går inte att säga närmare). Det är den
    konservativa riktningen — en vandrande klass blir ett grindfel hellre
    än att en verklig regression slinker igenom som "brus".
    """
    if own_failures_per_log is None:
        own_failures_per_log = _failures_per_log(logs)
    if not own_failures_per_log:
        return {}
    if len(own_failures_per_log) == 1:
        return {cls: STABLE for cls in own_failures_per_log[0]}
    total = len(own_failures_per_log)
    hits = {}
    for failures in own_failures_per_log:
        for cls in failures:
            hits[cls] = hits.get(cls, 0) + 1
    return {
        cls: (STABLE if count == total else FLAKY)
        for cls, count in hits.items()
    }


def _is_foreign(addon):
    """Sant om addon-namnet hör till en beroende modul, inte till oss."""
    if not addon or addon == MODULE_NAME:
        return False
    return any(addon.startswith(prefix) for prefix in FOREIGN_MODULE_PREFIXES)


@tagged('post_install', '-at_install')
class TestSuiteBaseline(TransactionCase):
    """Grinden: baslinjen är prövbar (test-suite-baseline 2.1 och 2.2)."""

    def test_baseline_has_a_reason_for_every_entry(self):
        """2.2/3.6: varje post ska bära ett skrivet skäl OCH en stabilitet."""
        known, foreign = _read_baseline()
        missing_reason = [
            f'{cls} ({count} fel)'
            for cls, count, reason, _stability in known + foreign
            if not (reason or '').strip()
        ]
        self.assertFalse(
            missing_reason,
            'Baslinjeposter utan skrivet skäl. Ett fel utan orsak är att '
            'sänka ribban i smyg — skriv VARFÖR felet är accepterat:\n  '
            + '\n  '.join(missing_reason),
        )
        # 3.6 (D4): en post utan stabilitet kan grinden inte tolka. Att
        # defaulta till `stabil` vore att göra en glömd post tyst till ett
        # grindfel — motsatsen till syftet.
        missing_stability = [
            f'{cls} ({count} fel)'
            for cls, count, _reason, stability in known + foreign
            if stability is None
        ]
        self.assertFalse(
            missing_stability,
            'Baslinjeposter utan stabilitetsangivelse. Ange "%s" (fallerar '
            'i alla mätta körningar) eller "%s" (fallerar i några):\n  '
            % (STABLE, FLAKY)
            + '\n  '.join(missing_stability),
        )

    def test_baseline_table_is_parseable(self):
        """Tabellen ska gå att läsa — annars är grinden blind.

        En tom tabell är grönt (baslinjen är noll sedan 2026-11-02), men
        filen måste finnas och rubrikerna måste finnas, annars tystnar
        jämförelsen utan att någon märker det.
        """
        self.assertTrue(
            BASELINE_PATH.is_file(),
            f'Baslinjen saknas: {BASELINE_PATH}')
        text = BASELINE_PATH.read_text(encoding='utf-8')
        self.assertIn('## Kända fel', text,
                      'BASELINE.md saknar rubriken "## Kända fel" — '
                      'jämförelsen skulle läsa fel tabell')
        known, foreign = _read_baseline()
        self.assertIsInstance(known, list)
        self.assertIsInstance(foreign, list)

    def test_no_new_failures_compared_to_baseline(self):
        """2.1/3.4: nya fel fäller; vandrande rapporteras men fäller inte.

        En klass som inte står i baslinjen fäller fortfarande — ett okänt
        fel är alltid värt att stoppa på (D1). En känd `vandrande` klass
        som fallerar i den här körningen loggas som varning: den är
        ordningsberoende brus, inte en regression (D3).
        """
        logs = _find_suite_logs()
        if not logs:
            self.skipTest(
                f'Ingen sviten-logg att jämföra mot. Sätt {SUITE_LOGS_ENV} '
                f'(kommaseparerad) eller {SUITE_LOG_ENV} till sökvägen för '
                f'körningen — grinden mäter, den gissar inte.')
        per_log = _failures_per_log(logs)
        if not per_log:
            self.skipTest(
                f'Ingen av loggarna {", ".join(str(p) for p in logs)} '
                f'innehåller en sammanfattning för {MODULE_NAME} '
                f'("Module {MODULE_NAME}: N failures, M errors") — de är '
                f'antingen trunkerade eller från en annan körning.')

        # Klasser som fallerade i NÅGON av loggarna — det är det grinden
        # jämför mot baslinjen. (Att bara titta på den sista loggen skulle
        # göra en klass som föll i logg 1 men inte 3 osynlig.)
        failed_anywhere = set()
        for own in per_log:
            failed_anywhere |= set(own)

        known, _foreign_table = _read_baseline()
        known_classes = {cls for cls, _count, _reason, _stab in known}
        new = sorted(failed_anywhere - known_classes)
        self.assertFalse(
            new,
            f'Nya fel i {MODULE_NAME} som inte står i baslinjen '
            f'({", ".join(str(p) for p in logs)}). Åtgärda dem, eller lägg '
            f'till dem i tests/BASELINE.md med ett skrivet skäl och en '
            f'stabilitet:\n  ' + '\n  '.join(new))

        # Vandrande klasser som föll i den här körningen: syns, fäller inte.
        measured = _classify_stability(logs, per_log)
        flaky_failing = sorted(
            cls for cls in failed_anywhere
            if cls in known_classes and measured.get(cls) == FLAKY)
        if flaky_failing:
            _logger.warning(
                'Vandrande fel i %s (fäller inte grinden — ordningsberoende '
                'i testfixturerna, se tests/BASELINE.md): %s',
                MODULE_NAME, ', '.join(flaky_failing))

    def test_baseline_entries_still_fail(self):
        """3.5: en post som slutat fallera ska bort — utom `vandrande`.

        En `vandrande` post som inte fallerar i den här körningen är
        förväntat — det är definitionen av vandrande (D5). Men en
        `vandrande` post som fallerar i ALLA lästa loggar har blivit
        stabil: då fäller grinden med ett meddelande om att posten ska
        flyttas. Det är den enda vägen en felklass får byta karaktär utan
        att någon tittar.
        """
        logs = _find_suite_logs()
        if not logs:
            self.skipTest(
                f'Ingen sviten-logg att jämföra mot ({SUITE_LOGS_ENV} / '
                f'{SUITE_LOG_ENV}).')
        per_log = _failures_per_log(logs)
        if not per_log:
            self.skipTest(
                f'Ingen av loggarna innehåller en sammanfattning för '
                f'{MODULE_NAME}.')
        failed_anywhere = set()
        for own in per_log:
            failed_anywhere |= set(own)
        measured = _classify_stability(logs, per_log)

        known, _foreign_table = _read_baseline()
        stale = [
            f'{cls} ({count} fel)'
            for cls, count, _reason, stability in known
            if cls not in failed_anywhere and stability != FLAKY
        ]
        self.assertFalse(
            stale,
            'Baslinjeposter som inte längre fallerar — ta bort dem. En '
            'kvarvarande rad döljer nästa överträdelse:\n  '
            + '\n  '.join(stale))

        # En `vandrande` post som blivit stabil: fäll med instruktionen.
        promoted = sorted(
            f'{cls} ({count} fel)'
            for cls, count, _reason, stability in known
            if stability == FLAKY and measured.get(cls) == STABLE
        )
        self.assertFalse(
            promoted,
            'Baslinjeposter markerade "%s" som fallerar i ALLA lästa loggar '
            '— de har gått från vandrande till stabil. Flytta dem till "%s" '
            'i tests/BASELINE.md:\n  ' % (FLAKY, STABLE)
            + '\n  '.join(promoted))

    def test_foreign_failures_are_not_counted(self):
        """Fel i beroende moduler ska hamna utanför jämförelsen.

        ``setUpClass``-felen i ``web*`` (``http_port()`` kräver en server)
        tillhör sina moduler. Om de räknades in skulle baslinjen fyllas av
        fel vi inte kan åtgärda — och grinden bli meningslös.
        """
        logs = _find_suite_logs()
        if not logs:
            self.skipTest(
                f'Ingen sviten-logg att jämföra mot ({SUITE_LOGS_ENV}).')
        _summary, own, foreign = _parse_suite_log(logs[0])
        self.assertFalse(
            set(own) & set(foreign),
            'Samma klassnamn räknas både som eget och främmande fel — '
            'addon-tolkningen i _parse_suite_log är fel: '
            + ', '.join(sorted(set(own) & set(foreign))))
        self.assertFalse(
            [cls for cls in own if _is_foreign(cls)],
            'En främmande moduls klassnamn har hamnat i de egna felen.')


if __name__ == '__main__':
    unittest.main()
