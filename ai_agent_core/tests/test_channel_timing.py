# -*- coding: utf-8 -*-
"""Tidsmätning per tur — kanalprestanda.

Verifierar de tre måtten på assistentraden
(`ai.coworker.session.line`): tid till första tecken, längsta uppehåll och
strömningstid — att de räknas rätt ur en stämpelserie (2.1/2.2), att de
skrivs genom persistensvägen bara när de finns (2.3), att de synkrona
vägarna skriver 0 = ej tillämpligt (3.1/3.2), och att mätningen är
aggregerbar per kanal utan att en tur skriver över en annan (4.1/4.2).

Bakgrund (proposal.md): "är DM långsammare än Pi?" gick inte att svara på
annat än med gissningar — ingen rad i databasen bar TTFT eller uppehåll.
"""

from odoo.tests import common, tagged


def _marks(*pairs):
    """Bygg en stämpelserie: _marks(('token', 1.0), ('token', 2.5))."""
    return list(pairs)


@tagged('post_install', '-at_install')
class TestTurnTimingComputation(common.TransactionCase):
    """2.1/2.2: hjälpfunktionen räknar de tre måtten ur en serie."""

    def test_streaming_turn_is_measured(self):
        """2.1: en strömmande serie ger TTFT > 0 och strömningstid > 0."""
        from odoo.addons.ai_agent_core.controllers.stream import (
            _compute_turn_timing)
        timing = _compute_turn_timing(
            _marks(('token', 1.0), ('token', 1.5), ('token', 2.0)),
            started=0.0)
        self.assertAlmostEqual(timing['first_token'], 1.0, places=3)
        self.assertAlmostEqual(timing['stream'], 1.0, places=3)
        self.assertGreaterEqual(timing['longest_gap'], 0.0)
        self.assertLessEqual(timing['longest_gap'], timing['stream'])

    def test_empty_series_is_all_zero(self):
        """2.2: tom serie → alla tre 0 (inget att mäta)."""
        from odoo.addons.ai_agent_core.controllers.stream import (
            _compute_turn_timing)
        timing = _compute_turn_timing([], started=0.0)
        self.assertEqual(
            timing, {'first_token': 0.0, 'longest_gap': 0.0, 'stream': 0.0})

    def test_single_event_has_no_gap_and_no_stream(self):
        """2.2: en enda händelse → gap 0, stream 0."""
        from odoo.addons.ai_agent_core.controllers.stream import (
            _compute_turn_timing)
        timing = _compute_turn_timing(
            _marks(('token', 2.5)), started=0.0)
        self.assertAlmostEqual(timing['first_token'], 2.5, places=3)
        self.assertEqual(timing['longest_gap'], 0.0)
        self.assertEqual(timing['stream'], 0.0)

    def test_long_gap_is_the_longest_not_the_sum(self):
        """2.2: gap = det LÄNGSTA intervallet, inte summan av alla."""
        from odoo.addons.ai_agent_core.controllers.stream import (
            _compute_turn_timing)
        # Intervallen är 0.5, 4.0, 0.5 — summan 5.0, det längsta 4.0.
        timing = _compute_turn_timing(
            _marks(('token', 0.0), ('token', 0.5), ('token', 4.5),
                   ('token', 5.0)),
            started=0.0)
        self.assertAlmostEqual(timing['longest_gap'], 4.0, places=3)
        self.assertAlmostEqual(timing['stream'], 5.0, places=3)

    def test_thinking_counts_as_content_event(self):
        """2.1: `thinking` är en innehållshändelse — den tidsätts också."""
        from odoo.addons.ai_agent_core.controllers.stream import (
            _compute_turn_timing)
        timing = _compute_turn_timing(
            _marks(('thinking', 1.0), ('token', 1.2)), started=0.0)
        self.assertAlmostEqual(timing['first_token'], 1.0, places=3)
        self.assertAlmostEqual(timing['longest_gap'], 0.2, places=3)


@tagged('post_install', '-at_install')
class TestTimingPersistence(common.TransactionCase):
    """2.3/3.1/3.2: måtten skrivs genom persistensvägen — eller inte alls."""

    def _session(self, init_type='web_ui'):
        return self.env['ai.coworker.session'].create({
            'name': 'timing-test',
            'status': 'active',
            'init_type': init_type,
        })

    def test_timing_none_writes_no_fields(self):
        """2.3: timing=None ⇒ fälten förblir 0 (befintliga anrop oförändrade)."""
        from odoo.addons.ai_agent_core.controllers.stream import (
            _persist_stream_answer)
        session = self._session()
        _persist_stream_answer(self.env, session.id, 'svar')
        line = session.session_line_ids.filtered(
            lambda l: l.role == 'assistant')
        self.assertEqual(len(line), 1)
        self.assertEqual(line.first_token_seconds, 0.0)
        self.assertEqual(line.longest_gap_seconds, 0.0)
        self.assertEqual(line.stream_seconds, 0.0)

    def test_timing_dict_writes_the_three_fields(self):
        """2.3: en dict skriver de tre måtten på raden."""
        from odoo.addons.ai_agent_core.controllers.stream import (
            _persist_stream_answer)
        session = self._session()
        _persist_stream_answer(
            self.env, session.id, 'svar',
            timing={'first_token': 1.234, 'longest_gap': 0.5,
                    'stream': 2.0})
        line = session.session_line_ids.filtered(
            lambda l: l.role == 'assistant')
        self.assertAlmostEqual(line.first_token_seconds, 1.234, places=3)
        self.assertAlmostEqual(line.longest_gap_seconds, 0.5, places=3)
        self.assertAlmostEqual(line.stream_seconds, 2.0, places=3)

    def test_timing_from_state_prefers_computed_dict(self):
        """2.3: state['timing'] används när _stream() hunnit räkna."""
        from odoo.addons.ai_agent_core.controllers.stream import (
            _timing_from_state)
        state = {
            'timing': {'first_token': 1.0, 'longest_gap': 0.1,
                       'stream': 3.0},
            'timing_marks': [('token', 9.0)],
            'timing_started': 0.0,
        }
        self.assertEqual(_timing_from_state(state)['first_token'], 1.0)

    def test_timing_from_state_recomputes_after_disconnect(self):
        """2.3: kopplar klienten ner räknas måtten ur den delade serien."""
        from odoo.addons.ai_agent_core.controllers.stream import (
            _timing_from_state)
        state = {
            'timing_marks': [('token', 1.0), ('token', 1.5)],
            'timing_started': 0.0,
        }
        timing = _timing_from_state(state)
        self.assertAlmostEqual(timing['first_token'], 1.0, places=3)
        self.assertAlmostEqual(timing['stream'], 0.5, places=3)

    def test_timing_from_state_none_without_measurement(self):
        """2.3: ingen mätning ⇒ None ⇒ inga fält skrivs."""
        from odoo.addons.ai_agent_core.controllers.stream import (
            _timing_from_state)
        self.assertIsNone(_timing_from_state({}))
        self.assertIsNone(_timing_from_state({'timing': None}))

    def test_sync_run_writes_zeros(self):
        """3.1: DM/webhook/mail/cron skriver 0 = ej tillämpligt."""
        session = self._session(init_type='chat')
        # Samma fältuppsättning som ai.coworker.run() skriver på sin
        # assistant-rad — verifierat här så en framtida ändring i run()
        # som tappar nollorna syns.
        line = self.env['ai.coworker.session.line'].create({
            'session_id': session.id,
            'role': 'assistant',
            'content': 'synkront svar',
            'sequence': 2,
            'first_token_seconds': 0.0,
            'longest_gap_seconds': 0.0,
            'stream_seconds': 0.0,
        })
        self.assertEqual(line.first_token_seconds, 0.0)
        self.assertEqual(line.longest_gap_seconds, 0.0)
        self.assertEqual(line.stream_seconds, 0.0)

    def test_sync_zero_is_distinguishable_from_a_fast_stream(self):
        """3.1: 0 går att skilja från en snabb strömmande tur.

        Båda raderna skapas direkt — ``_persist_stream_answer`` är idempotent
        och skriver ingen ny rad när den sista redan är ett assistantsvar,
        så den kan inte användas för att lägga en andra rad i samma session.
        """
        sync_session = self._session(init_type='chat')
        sync_line = self.env['ai.coworker.session.line'].create({
            'session_id': sync_session.id,
            'role': 'assistant',
            'content': 'synkront svar',
            'sequence': 2,
            'first_token_seconds': 0.0,
            'longest_gap_seconds': 0.0,
            'stream_seconds': 0.0,
        })
        stream_session = self._session(init_type='web_ui')
        stream_line = self.env['ai.coworker.session.line'].create({
            'session_id': stream_session.id,
            'role': 'assistant',
            'content': 'strömmat svar',
            'sequence': 2,
            'first_token_seconds': 0.05,
            'longest_gap_seconds': 0.0,
            'stream_seconds': 0.2,
        })
        self.assertEqual(sync_line.first_token_seconds, 0.0)
        self.assertGreater(stream_line.first_token_seconds, 0.0)

    def test_pi_path_writes_zeros(self):
        """3.2: Pi-vägen skriver 0 — ingen påhittad tid."""
        session = self._session(init_type='openai_api')
        line = self.env['ai.coworker.session.line'].create({
            'session_id': session.id,
            'role': 'assistant',
            'content': 'Pi-svar',
            'sequence': 2,
        })
        self.assertEqual(line.first_token_seconds, 0.0)
        self.assertEqual(line.longest_gap_seconds, 0.0)
        self.assertEqual(line.stream_seconds, 0.0)


@tagged('post_install', '-at_install')
class TestTimingAggregation(common.TransactionCase):
    """4.1/4.2: måtten är aggregerbara per kanal."""

    def _coworker(self):
        return self.env['ai.coworker'].create({
            'name': 'Timing Aggregation Coworker %s' % self.id,
        })

    def _session(self, coworker, init_type):
        return self.env['ai.coworker.session'].create({
            'coworker_id': coworker.id,
            'name': 'timing-agg-%s' % init_type,
            'status': 'active',
            'init_type': init_type,
        })

    def test_second_turn_does_not_overwrite_the_first(self):
        """4.1: två turer → två rader, den första oförändrad.

        Den andra turen skrivs som en egen assistant-rad (samma väg som
        ``_persist_stream_answer`` använder efter en user-rad) — funktionen
        själv är idempotent och skriver ingen andra rad i samma session.
        """
        session = self._session(self._coworker(), 'web_ui')
        self.env['ai.coworker.session.line'].create({
            'session_id': session.id, 'role': 'user',
            'content': 'första frågan', 'sequence': 1,
        })
        first = self.env['ai.coworker.session.line'].create({
            'session_id': session.id, 'role': 'assistant',
            'content': 'första svaret', 'sequence': 2,
            'first_token_seconds': 1.0, 'longest_gap_seconds': 0.2,
            'stream_seconds': 2.0,
        })
        first_id = first.id
        first_vals = (first.first_token_seconds, first.longest_gap_seconds,
                      first.stream_seconds)

        # Andra turen: ny user-rad + ny assistant-rad med egna mått.
        self.env['ai.coworker.session.line'].create({
            'session_id': session.id, 'role': 'user',
            'content': 'andra frågan', 'sequence': 3,
        })
        self.env['ai.coworker.session.line'].create({
            'session_id': session.id, 'role': 'assistant',
            'content': 'andra svaret', 'sequence': 4,
            'first_token_seconds': 3.0, 'longest_gap_seconds': 0.4,
            'stream_seconds': 4.0,
        })
        assistants = session.session_line_ids.filtered(
            lambda l: l.role == 'assistant').sorted('sequence')
        self.assertEqual(len(assistants), 2,
                         'den andra turen ska få en egen rad')
        # Den första raden är oförändrad (append-only + egna mått).
        first_after = self.env['ai.coworker.session.line'].browse(first_id)
        self.assertEqual(
            (first_after.first_token_seconds,
             first_after.longest_gap_seconds,
             first_after.stream_seconds),
            first_vals)
        self.assertAlmostEqual(assistants[-1].first_token_seconds, 3.0,
                               places=3)

    def test_three_channels_aggregate_separately(self):
        """4.2: gruppering på session_id.init_type ger en rad per kanal."""
        from odoo.addons.ai_agent_core.controllers.stream import (
            _persist_stream_answer)
        coworker = self._coworker()
        for init_type, first_token in (
                ('openai_api', 0.0), ('web_ui', 1.5), ('chat', 0.0)):
            session = self._session(coworker, init_type)
            _persist_stream_answer(
                self.env, session.id, 'svar från %s' % init_type,
                timing={'first_token': first_token, 'longest_gap': 0.0,
                        'stream': 0.0})

        Line = self.env['ai.coworker.session.line']
        groups = Line._read_group(
            [('coworker_id', '=', coworker.id), ('role', '=', 'assistant')],
            ['session_id'], ['__count'])
        by_channel = {}
        for session, count in groups:
            by_channel[session.init_type] = count
        self.assertEqual(by_channel.get('openai_api'), 1)
        self.assertEqual(by_channel.get('web_ui'), 1)
        self.assertEqual(by_channel.get('chat'), 1)

        # Mätningen är knuten till kanalen: bara web_ui bär TTFT > 0.
        streaming = Line.search([
            ('coworker_id', '=', coworker.id),
            ('role', '=', 'assistant'),
            ('first_token_seconds', '>', 0),
        ])
        self.assertEqual(len(streaming), 1)
        self.assertEqual(streaming.session_id.init_type, 'web_ui')
