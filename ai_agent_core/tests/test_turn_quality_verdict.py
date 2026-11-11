# -*- coding: utf-8 -*-
"""Grind: en tur mäts på BÅDE kvalitet och tid (tool-call-quality 7.3).

VARFÖR DEN HÄR FILEN FINNS: `perf/measure_turn_quality.py` är ett
mätskript — det skriver en tabell och kan inte gå rött. Utan ett test är
det ingen grind, bara en rapport.

Testerna prövar BEDÖMNINGEN, inte modellen: de matar in kända utfall och
kräver att rätt dimension fäller körningen. Att anropa en riktig provider
hade gjort testet långsamt, icke-deterministiskt och beroende av en nyckel —
och hade inte sagt något om det som faktiskt testas (att "snabbt men fel"
och "rätt men långsamt" fälls på olika grunder).

Den riktiga körningen mot en levande tur hör till driftstoppet.
"""

import importlib.util
import pathlib

from odoo.tests import common, tagged

#: `perf/` ligger utanför paketet (ingen `__init__.py`) — samma
#: importmönster som `test_measure_channels.py` använder.
SCRIPT_PATH = (pathlib.Path(__file__).resolve().parent
               / 'perf' / 'measure_turn_quality.py')


def _load_measure():
    spec = importlib.util.spec_from_file_location(
        'measure_turn_quality_tool', str(SCRIPT_PATH))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


M = _load_measure()


def _verdict(first_token=1.0, tool_calls=2, answer='Skanska har möten'):
    """Bygg ett utfall och kör samma bedömning som mätningen gör."""
    answer_lower = answer.lower()
    missing = [w for w in M.EXPECTED_CONTAINS if w.lower() not in answer_lower]
    forbidden = [w for w in M.EXPECTED_NOT_CONTAINS
                 if w.lower() in answer_lower]
    quality_ok = not missing and not forbidden

    reasons = []
    if not quality_ok:
        if missing:
            reasons.append('kvalitet: saknar %s' % ', '.join(missing))
        if forbidden:
            reasons.append('kvalitet: innehåller %s' % ', '.join(forbidden))
    if first_token > M.MAX_FIRST_TOKEN_SECONDS:
        reasons.append('tid: TTFT %.1fs > %.0fs'
                       % (first_token, M.MAX_FIRST_TOKEN_SECONDS))
    if tool_calls > M.MAX_TOOL_CALLS:
        reasons.append('tid: %d verktygsanrop > %d'
                       % (tool_calls, M.MAX_TOOL_CALLS))
    return {
        'quality_ok': quality_ok,
        'verdict': 'PASS' if not reasons else 'FAIL',
        'reasons': reasons,
    }


@tagged('post_install', '-at_install', 'tool_quality')
class TestTurnQualityVerdict(common.TransactionCase):
    """Bedömningen: båda dimensionerna måste hålla."""

    def test_good_turn_passes(self):
        out = _verdict()
        self.assertEqual(out['verdict'], 'PASS')
        self.assertEqual(out['reasons'], [])

    def test_fast_but_wrong_fails_on_quality(self):
        """Snabbt men fel får INTE passera."""
        out = _verdict(first_token=0.5, tool_calls=1,
                       answer='Jag kan inte svara på det.')
        self.assertEqual(out['verdict'], 'FAIL')
        self.assertTrue(
            any('kvalitet' in r for r in out['reasons']),
            'orsaken ska vara kvaliteten, inte tiden: %s' % out['reasons'])
        self.assertFalse(
            any('tid:' in r for r in out['reasons']),
            'tiden var bra och ska inte rapporteras som orsak')

    def test_correct_but_slow_fails_on_time(self):
        """Rätt men långsamt får INTE heller passera."""
        out = _verdict(first_token=M.MAX_FIRST_TOKEN_SECONDS + 5,
                       tool_calls=2, answer='Skanska har möten i veckan')
        self.assertEqual(out['verdict'], 'FAIL')
        self.assertTrue(
            any('tid:' in r for r in out['reasons']),
            'orsaken ska vara tiden: %s' % out['reasons'])
        self.assertFalse(
            any('kvalitet' in r for r in out['reasons']),
            'kvaliteten var god och ska inte rapporteras som orsak')

    def test_too_many_tool_calls_fails(self):
        """Session 28097 gjorde 13 anrop — det ska fällas."""
        out = _verdict(tool_calls=13)
        self.assertEqual(out['verdict'], 'FAIL')
        self.assertTrue(any('verktygsanrop' in r for r in out['reasons']))

    def test_both_dimensions_can_fail_together(self):
        """Båda kan fallera — och båda orsakerna ska namnges."""
        out = _verdict(first_token=60, tool_calls=13,
                       answer='(inget svar)')
        self.assertEqual(out['verdict'], 'FAIL')
        self.assertTrue(any('kvalitet' in r for r in out['reasons']))
        self.assertTrue(any('tid:' in r for r in out['reasons']))

    def test_empty_answer_is_not_a_pass(self):
        out = _verdict(answer='')
        self.assertEqual(out['verdict'], 'FAIL')


@tagged('post_install', '-at_install', 'tool_quality')
class TestMeasureQuestionShape(common.TransactionCase):
    """Frågan ska kräva flera källor (task 7.1)."""

    def test_question_mentions_mail_and_calendar(self):
        q = M.MEASURE_QUESTION.lower()
        self.assertTrue(
            any(w in q for w in ('mail', 'skrev')),
            'frågan ska kräva mail')
        self.assertTrue(
            any(w in q for w in ('möte', 'möten', 'kalender', 'vecka')),
            'frågan ska kräva kalender')

    def test_question_requires_a_related_object(self):
        """Uppgifter/kunder — den tredje källan."""
        q = M.MEASURE_QUESTION.lower()
        self.assertTrue(
            any(w in q for w in ('uppgift', 'uppgifter', 'kund', 'hör till')),
            'frågan ska kräva ett relaterat objekt')

    def test_expected_contains_names_the_customer(self):
        self.assertTrue(
            any('skanska' in w.lower() for w in M.EXPECTED_CONTAINS))

    def test_limits_are_defined(self):
        self.assertGreater(M.MAX_FIRST_TOKEN_SECONDS, 0)
        self.assertGreater(M.MAX_TOOL_CALLS, 0)
