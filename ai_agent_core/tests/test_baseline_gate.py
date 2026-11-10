# -*- coding: utf-8 -*-
"""Tester för grinden själv — `test-baseline-determinism` 4.1-4.5.

Grinden (`test_baseline.py`) ska vara **deterministisk i sitt utfall**:
samma kod ska ge samma grönt/rött, oavsett vilka vandrande fel som råkade
inträffa. Testerna här prövar den mekanismen direkt — de skapar egna
loggar och egna baslinjetabeller i en tempkatalog, så de varken rör
`tests/BASELINE.md` eller kräver en riktig svitkörning.

Regeln (4.5): varje test här ska fallera på koden FÖRE ändringen. Det
bevisas av att de prövar funktioner som inte fanns
(`_find_suite_logs`, `_classify_stability`, `_parse_stability`) och
beteenden som den gamla grinden inte hade (vandrande fäller inte).
"""

import pathlib
import tempfile

from odoo.tests import common, tagged

from odoo.addons.ai_agent_core.tests import test_baseline as gate


def _log(summary_failures=1, summary_errors=0, classes=()):
    """En minimal svitlogg: sammanfattning + en FAIL-rad per klass."""
    lines = [
        '2026-11-09 10:00:00,000 1 INFO db odoo.modules.loading: '
        'Module ai_agent_core: %d failures, %d errors of 10 tests'
        % (summary_failures, summary_errors),
    ]
    for cls in classes:
        lines.append(
            '2026-11-09 10:00:01,000 1 ERROR db '
            'odoo.addons.ai_agent_core.tests.test_x: FAIL: %s.test_y' % cls)
    return '\n'.join(lines) + '\n'


def _baseline(rows):
    """En BASELINE.md-text med givna (klass, antal, orsak, stabilitet)-rader."""
    header = (
        '# Baslinje\n\n'
        '## Kända fel\n\n'
        '| Test | Fel | Orsak | Stabilitet |\n'
        '|---|---|---|---|\n')
    body = ''.join(
        '| `%s` | %s | %s | %s |\n' % (cls, count, reason, stability)
        for cls, count, reason, stability in rows)
    return header + body


@tagged('post_install', '-at_install')
class TestStabilityParsing(common.TransactionCase):
    """3.1/3.3: stabilitet läses ur baslinjen och klassas ur loggar."""

    def _write(self, tmpdir, name, text):
        path = pathlib.Path(tmpdir) / name
        path.write_text(text, encoding='utf-8')
        return path

    def test_read_baseline_returns_four_tuple(self):
        """3.1: `_read_baseline()` ger (klass, antal, orsak, stabilitet)."""
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(tmp, 'BASELINE.md', _baseline([
                ('TestA', 3, 'skäl A', 'stabil'),
                ('TestB', 1, 'skäl B', 'vandrande'),
            ]))
            known, foreign = gate._read_baseline(path)
        self.assertEqual(len(known), 2)
        # Klassnamnet normaliseras utan backticks, så det matchar
        # `_parse_suite_log()`s nakna namn (se `_parse_class`).
        self.assertEqual(known[0][0], 'TestA')
        self.assertEqual(known[0][1:], (3, 'skäl A', 'stabil'))
        self.assertEqual(known[1][1:], (1, 'skäl B', 'vandrande'))
        self.assertEqual(foreign, [])

    def test_entry_without_stability_gives_none(self):
        """3.1/D4: en post utan stabilitet ger None — inte en default."""
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(tmp, 'BASELINE.md', _baseline([
                ('TestA', 3, 'skäl A', ''),
            ]))
            known, _foreign = gate._read_baseline(path)
        self.assertEqual(len(known), 1)
        self.assertIsNone(known[0][3])

    def test_parse_stability_accepts_markdown_emphasis(self):
        """3.1: `*stabil*` i en formaterad tabell tystnar inte."""
        self.assertEqual(gate._parse_stability('stabil'), 'stabil')
        self.assertEqual(gate._parse_stability('*vandrande*'), 'vandrande')
        self.assertEqual(gate._parse_stability('Stabil'), 'stabil')
        self.assertIsNone(gate._parse_stability('kanske'))
        self.assertIsNone(gate._parse_stability(''))

    def test_classify_stable_when_in_all_logs(self):
        """3.3: fallerar i alla loggar → stabil."""
        measured = gate._classify_stability([], [
            {'TestA': 1}, {'TestA': 1}, {'TestA': 1}])
        self.assertEqual(measured, {'TestA': 'stabil'})

    def test_classify_flaky_when_in_some_logs(self):
        """3.3: fallerar i några men inte alla → vandrande."""
        measured = gate._classify_stability([], [
            {'TestA': 1, 'TestB': 1}, {'TestA': 1}, {'TestA': 1}])
        self.assertEqual(measured['TestA'], 'stabil')
        self.assertEqual(measured['TestB'], 'vandrande')

    def test_classify_with_single_log_is_stable(self):
        """3.3: med en enda logg kan stabilitet inte avgöras → stabil."""
        measured = gate._classify_stability([], [{'TestA': 1}])
        self.assertEqual(measured, {'TestA': 'stabil'})

    def test_find_suite_logs_reads_the_comma_separated_list(self):
        """3.2: `$AI_AGENT_CORE_SUITE_LOGS` läses som en lista."""
        with tempfile.TemporaryDirectory() as tmp:
            first = self._write(tmp, 'r1.log', _log(classes=('TestA',)))
            second = self._write(tmp, 'r2.log', _log(classes=('TestB',)))
            missing = pathlib.Path(tmp) / 'finns-inte.log'
            old = gate.os.environ.get(gate.SUITE_LOGS_ENV)
            gate.os.environ[gate.SUITE_LOGS_ENV] = '%s,%s,%s' % (
                first, second, missing)
            try:
                logs = gate._find_suite_logs()
            finally:
                if old is None:
                    gate.os.environ.pop(gate.SUITE_LOGS_ENV, None)
                else:
                    gate.os.environ[gate.SUITE_LOGS_ENV] = old
        self.assertEqual(logs, [first, second],
                         'bara existerande loggar, i listans ordning')


@tagged('post_install', '-at_install')
class TestGateOutcome(common.TransactionCase):
    """4.1-4.4: grinden är deterministisk i sitt utfall."""

    def setUp(self):
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        # Rör inte tests/BASELINE.md — peka om grinden mot en tempfil.
        self._orig_baseline = gate.BASELINE_PATH
        self.addCleanup(setattr, gate, 'BASELINE_PATH', self._orig_baseline)
        self._orig_logs = gate._find_suite_logs
        self.addCleanup(setattr, gate, '_find_suite_logs', self._orig_logs)

    def _setup(self, rows, logs):
        """Skriv en baslinje + loggar och peka om grinden mot dem."""
        path = pathlib.Path(self._tmp.name) / 'BASELINE.md'
        path.write_text(_baseline(rows), encoding='utf-8')
        gate.BASELINE_PATH = path
        paths = []
        for i, classes in enumerate(logs):
            log = pathlib.Path(self._tmp.name) / ('run%d.log' % i)
            log.write_text(_log(classes=classes), encoding='utf-8')
            paths.append(log)
        gate._find_suite_logs = lambda: paths
        # En riktig instans behövs: `assertFalse`/`assertRaises` är
        # unittest-metoder på TestCase, inte på klassen. Vi anropar bara
        # grindens kontrollmetoder — de rör ingen cursor, bara filer — så
        # ingen setUp behövs.
        return gate.TestSuiteBaseline('test_baseline_table_is_parseable')

    def test_entry_without_stability_fails(self):
        """4.1: en post utan stabilitet fäller grinden."""
        gate_obj = self._setup(
            [('TestA', 1, 'skäl', '')], [('TestA',), ('TestA',)])
        with self.assertRaises(AssertionError) as ctx:
            gate_obj.test_baseline_has_a_reason_for_every_entry()
        self.assertIn('stabilitet', str(ctx.exception).lower())

    def test_flaky_failing_in_one_of_two_logs_does_not_fail(self):
        """4.2: en vandrande klass i en av två loggar fäller INTE."""
        gate_obj = self._setup(
            [('TestA', 1, 'skäl', 'stabil'),
             ('TestB', 1, 'skäl', 'vandrande')],
            [('TestA', 'TestB'), ('TestA',)])
        # Ska inte kasta.
        gate_obj.test_no_new_failures_compared_to_baseline()
        gate_obj.test_baseline_entries_still_fail()

    def test_flaky_failing_in_all_logs_fails_as_promoted(self):
        """4.3: en vandrande klass i ALLA loggar fäller som befordrad."""
        gate_obj = self._setup(
            [('TestA', 1, 'skäl', 'stabil'),
             ('TestB', 1, 'skäl', 'vandrande')],
            [('TestA', 'TestB'), ('TestA', 'TestB')])
        with self.assertRaises(AssertionError) as ctx:
            gate_obj.test_baseline_entries_still_fail()
        message = str(ctx.exception)
        self.assertIn('vandrande till stabil', message)
        self.assertIn('TestB', message)

    def test_new_class_outside_baseline_still_fails(self):
        """4.4: en klass utanför baslinjen fäller fortfarande."""
        gate_obj = self._setup(
            [('TestA', 1, 'skäl', 'stabil')],
            [('TestA', 'TestRegression'), ('TestA',)])
        with self.assertRaises(AssertionError) as ctx:
            gate_obj.test_no_new_failures_compared_to_baseline()
        self.assertIn('TestRegression', str(ctx.exception))

    def test_same_code_gives_same_verdict_regardless_of_flaky(self):
        """4.2/5.1: samma kod ger samma grönt oavsett vandrande utfall."""
        rows = [('TestA', 1, 'skäl', 'stabil'),
                ('TestB', 1, 'skäl', 'vandrande')]
        # Två scenarier med samma kod men olika vandrande utfall.
        for logs in ((('TestA', 'TestB'), ('TestA',)),
                     (('TestA',), ('TestA', 'TestB'))):
            gate_obj = self._setup(rows, logs)
            # Ingen av körningarna får fälla grinden.
            gate_obj.test_no_new_failures_compared_to_baseline()
            gate_obj.test_baseline_entries_still_fail()
