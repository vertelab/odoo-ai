# -*- coding: utf-8 -*-
"""Tester för jämförelseverktyget `tests/perf/measure_channels.py`.

Verktyget är ett fristående skript (kanalprestanda D5), men dess
insamling, rapport och omätt-grind ska vara prövbar — annars är
"reproducerbart" ett påstående. Testerna importerar skriptet som modul och
kör dess funktioner mot testdatabasen.
"""

import importlib.util
import io
import pathlib

from odoo.tests import common, tagged

SCRIPT_PATH = (pathlib.Path(__file__).resolve().parent
               / 'perf' / 'measure_channels.py')

#: Samma ``init_type``-axel som verktyget grupperar på.
CHANNELS = ('openai_api', 'web_ui', 'chat')


def _load_tool():
    """Importera measure_channels som en modul (den ligger utanför paketet)."""
    spec = importlib.util.spec_from_file_location(
        'measure_channels_tool', str(SCRIPT_PATH))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@tagged('post_install', '-at_install')
class TestMeasureChannelsHelpers(common.TransactionCase):
    """5.1/5.3: skriptet importeras och statistiken räknas rätt."""

    def test_script_imports(self):
        """5.1: skriptet går att importera utan spårback."""
        tool = _load_tool()
        self.assertTrue(callable(tool.main))
        self.assertTrue(callable(tool._collect_session_lines))
        self.assertEqual(tool.CHANNELS, CHANNELS)

    def test_median_and_p95_on_known_series(self):
        """5.3: median/p95 stämmer mot en känd liten serie."""
        tool = _load_tool()
        series = [1, 2, 3, 4, 5]
        self.assertEqual(tool._median(series), 3)
        # nearest-rank på 5 värden: ceil(4.75) - 1 = index 4 → 5
        self.assertEqual(tool._p95(series), 5)

    def test_median_is_the_middle_of_an_even_series(self):
        """5.3: jämn längd → medelvärdet av de två mittersta."""
        tool = _load_tool()
        self.assertEqual(tool._median([1, 2, 3, 4]), 2.5)

    def test_p95_is_not_max_for_larger_series(self):
        """5.3: p95 får inte vara maxvärdet för en stor serie."""
        tool = _load_tool()
        values = list(range(1, 21))  # 1..20
        self.assertEqual(tool._p95(values), 19)

    def test_empty_series_gives_none(self):
        """5.3: tom serie → None (ingen gissning)."""
        tool = _load_tool()
        self.assertIsNone(tool._median([]))
        self.assertIsNone(tool._p95([]))


@tagged('post_install', '-at_install')
class TestMeasureChannelsCollection(common.TransactionCase):
    """5.2/5.4/5.5: insamling, omätt-grind och retry-räkning."""

    def _coworker(self):
        return self.env['ai.coworker'].create({
            'name': 'Measure Channels Coworker %s' % self.id,
        })

    def _session(self, coworker, init_type, name=None):
        return self.env['ai.coworker.session'].sudo().create({
            'coworker_id': coworker.id,
            'name': name or ('perf-channel-%s-%s' % (init_type, self.id)),
            'status': 'active',
            'init_type': init_type,
        })

    def _line(self, session, role, content, sequence, **kw):
        vals = {
            'session_id': session.id,
            'role': role,
            'content': content,
            'sequence': sequence,
        }
        vals.update(kw)
        return self.env['ai.coworker.session.line'].sudo().create(vals)

    def test_collects_only_its_own_sessions(self):
        """5.2: bara de sessioner körningen skapade — ingen tidsfiltrering."""
        tool = _load_tool()
        coworker = self._coworker()
        mine = self._session(coworker, 'web_ui')
        foreign = self._session(coworker, 'web_ui',
                                name='annan-trafik-%s' % self.id)
        self._line(mine, 'user', 'fråga', 1)
        self._line(mine, 'assistant', 'svar', 2,
                   first_token_seconds=0.5, stream_seconds=1.0)
        self._line(foreign, 'user', 'gammal', 1)
        self._line(foreign, 'assistant', 'gammalt svar', 2,
                   first_token_seconds=9.0, stream_seconds=9.0)

        points = tool._collect_session_lines(self.env, [mine.id])
        self.assertEqual(len(points), 1)
        self.assertEqual(points[0]['channel'], 'web_ui')
        self.assertAlmostEqual(points[0]['ttft'], 0.5, places=3)

    def test_repeat_gives_one_point_per_answer(self):
        """5.2: `--repeat 2` ger exakt 2 mätpunkter per kanal."""
        tool = _load_tool()
        coworker = self._coworker()
        session_ids = []
        for channel in ('openai_api', 'web_ui', 'chat'):
            for i in range(2):
                session = self._session(
                    coworker, channel,
                    name='perf-channel-%s-%s-%d' % (channel, self.id, i))
                session_ids.append(session.id)
                self._line(session, 'user', 'fråga', 1)
                self._line(session, 'assistant', 'svar', 2,
                           first_token_seconds=0.1 * (i + 1),
                           stream_seconds=0.2)
        points = tool._collect_session_lines(self.env, session_ids)
        for channel in ('openai_api', 'web_ui', 'chat'):
            self.assertEqual(
                len([p for p in points if p['channel'] == channel]), 2,
                '%s ska ha exakt 2 mätpunkter' % channel)

    def test_unmeasured_channel_fails_the_run(self):
        """5.4: en kanal med n == 0 rapporteras omätt och main() ger fel."""
        tool = _load_tool()
        coworker = self._coworker()
        # Två av tre kanaler har rader; `chat` saknar helt mätpunkter.
        session_ids = []
        for channel in ('openai_api', 'web_ui'):
            session = self._session(
                coworker, channel,
                name='perf-channel-%s-unmet-%s' % (channel, self.id))
            session_ids.append(session.id)
            self._line(session, 'user', 'fråga', 1)
            self._line(session, 'assistant', 'svar', 2, stream_seconds=0.2)
        points = tool._collect_session_lines(self.env, session_ids)

        out = io.StringIO()
        measured = tool._report(points, out)
        self.assertEqual(measured.get('openai_api'), 1)
        self.assertEqual(measured.get('web_ui'), 1)
        self.assertEqual(measured.get('chat'), 0)
        self.assertIn('OMÄTT', out.getvalue())

    def test_retry_is_a_separate_measurement(self):
        """5.5: samma content_hash men annat tokenantal = egen mätpunkt."""
        tool = _load_tool()
        coworker = self._coworker()
        session = self._session(coworker, 'web_ui',
                                name='perf-channel-retry-%s' % self.id)
        self._line(session, 'user', 'fråga', 1)
        # Två assistentrader med IDENTISKT innehåll (→ samma hash) men
        # olika tokenantal: det andra är ett riktigt, betalat retry-anrop.
        self._line(session, 'assistant', 'samma svar', 2, token_output=10,
                   first_token_seconds=0.2, stream_seconds=0.5)
        self._line(session, 'assistant', 'samma svar', 3, token_output=11,
                   first_token_seconds=0.3, stream_seconds=0.6)
        points = tool._collect_session_lines(self.env, [session.id])
        self.assertEqual(len(points), 2,
                         'ett retry är en egen mätpunkt, inte en sammanslagning')

    def test_total_time_comes_from_create_date_delta(self):
        """5.2: total väggklocka läses ur user- → assistant-radens delta."""
        tool = _load_tool()
        coworker = self._coworker()
        session = self._session(coworker, 'web_ui',
                                name='perf-channel-total-%s' % self.id)
        self._line(session, 'user', 'fråga', 1)
        self._line(session, 'assistant', 'svar', 2,
                   first_token_seconds=0.1, stream_seconds=0.4)
        points = tool._collect_session_lines(self.env, [session.id])
        self.assertEqual(len(points), 1)
        self.assertIsNotNone(points[0]['total'])
        self.assertGreaterEqual(points[0]['total'], 0.0)
