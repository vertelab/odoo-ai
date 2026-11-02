# -*- coding: utf-8 -*-
"""Grind: en testkörning får inte tillföra fel som inte står i baslinjen.

Ändring: test-suite-baseline (uppgift 2.1 och 2.2).

VARFÖR: ``BASELINE.md`` skrev ned svitens kända fel — men en nedskriven
tabell som ingen läser är en påminnelse, inte en grind. Den här filen gör
baslinjen prövbar: den läser tabellen, läser en körd sviten-logg och
jämför.

Grinden fallerar på tre saker:

1. **Ett nytt fel.** En klass i loggen som inte står i baslinjen.
2. **En post utan skäl.** En rad i baslinjetabellen vars orsakskolumn är
   tom — "att lägga till ett fel utan skäl är att sänka ribban i smyg".
3. **En post som inte längre fallerar.** Baslinjen ska krympa när ett fel
   åtgärdas; en kvarvarande rad döljer nästa överträdelse.

Felen i BEROENDE moduler (``setUpClass`` i ``web``, ``web_editor``,
``web_pwa_*``) räknas inte in — de tillhör sina moduler och står under
rubriken "utanför `ai_agent_core`" i ``BASELINE.md``.

Körning::

    odoo --test-tags=/ai_agent_core:TestSuiteBaseline
    # loggen läses från $AI_AGENT_CORE_SUITE_LOG eller en sökväg i
    # /tmp/suite.log, /tmp/tst_*.log

Utan en logg att läsa hoppar jämförelsen över sig själv (den kan inte
mäta något), men tabellens innehåll prövas alltid.
"""

import os
import pathlib
import re
import unittest

from odoo.tests import TransactionCase, tagged


MODULE_ROOT = pathlib.Path(__file__).resolve().parent.parent
BASELINE_PATH = MODULE_ROOT / 'tests' / 'BASELINE.md'

MODULE_NAME = 'ai_agent_core'

#: Miljövariabel som pekar ut loggen. Sätts av den som kör sviten så att
#: grinden läser exakt den körning som just gjordes.
SUITE_LOG_ENV = 'AI_AGENT_CORE_SUITE_LOG'

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


def _read_baseline(path=BASELINE_PATH):
    """Läs baslinjens tabeller. Returnerar ``(kända, främmande)``.

    ``kända``    — ``[(klass, antal, orsak)]`` för ``ai_agent_core``.
    ``främmande`` — samma form, men för moduler utanför baslinjen.

    Tabellerna skiljs av rubriken ``utanför``: allt före den hör till
    modulen, allt efter till beroendena. En rubrikrad i markdown börjar
    med ``#``; kolumnrubriken (``| Kategori | …``) hoppas över eftersom
    dess första cell inte går att göra om till ett antal.
    """
    known, foreign = [], []
    target = known
    for line in path.read_text(encoding='utf-8').splitlines():
        stripped = line.strip()
        if stripped.startswith('#') and FOREIGN_SECTION in stripped.lower():
            target = foreign
            continue
        if not stripped.startswith('|'):
            continue
        cells = [c.strip() for c in stripped.strip('|').split('|')]
        if len(cells) < 3:
            continue
        count = _parse_count(cells[1])
        if count is None:
            continue  # kolumnrubrik eller avgränsarrad
        target.append((cells[0], count, ' '.join(cells[2:]).strip()))
    return known, foreign


def _parse_count(cell):
    """Antal ur en cell: ``22``, ``125 (…)`` eller ``—`` → ``None``.

    ``—`` betyder "ingen siffra", inte noll — en rad utan antal kan inte
    jämföras mot loggen och ska därför inte räknas som en känd klass.
    """
    match = re.match(r'(\d+)', cell)
    return int(match.group(1)) if match else None


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


def _is_foreign(addon):
    """Sant om addon-namnet hör till en beroende modul, inte till oss."""
    if not addon or addon == MODULE_NAME:
        return False
    return any(addon.startswith(prefix) for prefix in FOREIGN_MODULE_PREFIXES)


@tagged('post_install', '-at_install')
class TestSuiteBaseline(TransactionCase):
    """Grinden: baslinjen är prövbar (test-suite-baseline 2.1 och 2.2)."""

    def test_baseline_has_a_reason_for_every_entry(self):
        """2.2: varje post i baslinjen ska bära ett skrivet skäl."""
        known, foreign = _read_baseline()
        missing = [
            f'{cls} ({count} fel)'
            for cls, count, reason in known + foreign
            if not (reason or '').strip()
        ]
        self.assertFalse(
            missing,
            'Baslinjeposter utan skrivet skäl. Ett fel utan orsak är att '
            'sänka ribban i smyg — skriv VARFÖR felet är accepterat:\n  '
            + '\n  '.join(missing),
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
        """2.1: en klass som fallerar utan att stå i baslinjen fallerar grinden."""
        log = _find_suite_log()
        if log is None:
            self.skipTest(
                f'Ingen sviten-logg att jämföra mot. Sätt {SUITE_LOG_ENV} '
                f'till sökvägen för körningen — grinden mäter, den gissar '
                f'inte.')
        summary, own, foreign = _parse_suite_log(log)
        if MODULE_NAME not in summary:
            self.skipTest(
                f'{log} innehåller ingen sammanfattning för {MODULE_NAME} '
                f'("Module {MODULE_NAME}: N failures, M errors") — filen är '
                f'antingen trunkerad eller från en annan körning.')

        known, _foreign_table = _read_baseline()
        known_classes = {cls for cls, _count, _reason in known}
        new = sorted(
            f'{cls} ({count} fel)' for cls, count in own.items()
            if cls not in known_classes)
        self.assertFalse(
            new,
            f'Nya fel i {MODULE_NAME} som inte står i baslinjen '
            f'({log}). Åtgärda dem, eller lägg till dem i '
            f'tests/BASELINE.md med ett skrivet skäl:\n  '
            + '\n  '.join(new))

    def test_baseline_entries_still_fail(self):
        """En post som inte längre fallerar ska bort ur baslinjen."""
        log = _find_suite_log()
        if log is None:
            self.skipTest(
                f'Ingen sviten-logg att jämföra mot ({SUITE_LOG_ENV}).')
        summary, own, _foreign = _parse_suite_log(log)
        if MODULE_NAME not in summary:
            self.skipTest(
                f'{log} innehåller ingen sammanfattning för {MODULE_NAME}.')
        known, _foreign_table = _read_baseline()
        stale = [
            f'{cls} ({count} fel)'
            for cls, count, _reason in known
            if cls not in own
        ]
        self.assertFalse(
            stale,
            'Baslinjeposter som inte längre fallerar — ta bort dem. En '
            'kvarvarande rad döljer nästa överträdelse:\n  '
            + '\n  '.join(stale))

    def test_foreign_failures_are_not_counted(self):
        """Fel i beroende moduler ska hamna utanför jämförelsen.

        ``setUpClass``-felen i ``web*`` (``http_port()`` kräver en server)
        tillhör sina moduler. Om de räknades in skulle baslinjen fyllas av
        fel vi inte kan åtgärda — och grinden bli meningslös.
        """
        log = _find_suite_log()
        if log is None:
            self.skipTest(f'Ingen sviten-logg att jämföra mot ({SUITE_LOG_ENV}).')
        _summary, own, foreign = _parse_suite_log(log)
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
