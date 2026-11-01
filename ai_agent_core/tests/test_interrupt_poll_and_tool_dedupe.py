# -*- coding: utf-8 -*-
"""Interrupt-pollens tidsgräns (A6) och tool-rads-dedupe (B1).

A6 — `/ai/interrupt/poll` blockerade en worker för evigt
    `while True: ... time.sleep(0.5)` i en WSGI-tråd. Utan timeout hölls
    workern tills ett interrupt dök upp — eller för alltid. Några samtidiga
    webbsessioner räckte för att tömma worker-poolen, och då slutade HELA
    Odoo svara, inte bara AI-vyn.

B1 — Verktygsrader tappades
    `_persist_stream_tool_lines` dedupade på `tool_name`. En tur som anropade
    `odoo_search` två gånger med olika argument skrev bara EN rad — den
    andra sökningen försvann ur historiken, och ett återupptaget samtal
    saknade sitt verktygsspår.
"""

import time

from odoo.tests.common import TransactionCase, tagged

from odoo.addons.ai_agent_core.controllers.stream import (
    INTERRUPT_POLL_TIMEOUT,
    _persist_stream_tool_lines,
)


@tagged('ai_core', 'post_install', '-at_install')
class TestInterruptPollTimeout(TransactionCase):
    """A6: poll-loopen måste ha ett tak."""

    def test_timeout_constant_is_bounded(self):
        self.assertGreater(INTERRUPT_POLL_TIMEOUT, 0)
        self.assertLessEqual(
            INTERRUPT_POLL_TIMEOUT, 60,
            'en poll som håller en worker längre än 60 s tömmer poolen')

    def test_poll_loop_has_deadline(self):
        """Strukturell grind: inga obegränsade `while`-loopar i rutten.

        Vi tittar på AST:en, inte på texten — en `while True` i en KOMMENTAR
        är ofarlig, och ett textbaserat test hade både gett falska träffar
        (som här) och missat en loop skriven på annat sätt.
        """
        import ast
        import inspect
        import textwrap
        from odoo.addons.ai_agent_core.controllers import stream as stream_mod

        src = textwrap.dedent(inspect.getsource(
            stream_mod.AIStreamController.interrupt_poll))
        tree = ast.parse(src)

        unbounded = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.While):
                continue
            test = node.test
            # while True / while 1 → obegränsad
            if isinstance(test, ast.Constant) and test.value:
                unbounded.append(node.lineno)

        self.assertEqual(
            unbounded, [],
            'interrupt_poll får inte ha en obegränsad while-loop — den '
            'håller en WSGI-worker och tömmer poolen')
        self.assertIn('INTERRUPT_POLL_TIMEOUT', src,
                      'poll-loopen måste använda den bundna tidsgränsen')

@tagged('ai_core', 'post_install', '-at_install')
class TestToolLineDedupe(TransactionCase):
    """B1: två anrop av samma verktyg ska ge två rader."""

    def setUp(self):
        super().setUp()
        self.coworker = self.env['ai.coworker'].create({
            'name': 'Tool Line Dedupe Test',
        })
        self.session = self.env['ai.coworker.session'].create({
            'coworker_id': self.coworker.id,
            'name': 'Dedupe session',
            'status': 'active',
            'user_id': self.env.user.id,
        })

    def _loop(self, history):
        class _Loop:
            tool_history = history
        return _Loop()

    def _tool_lines(self):
        return self.env['ai.coworker.session.line'].search([
            ('session_id', '=', self.session.id),
            ('role', '=', 'tool'),
        ])

    def test_two_calls_same_tool_two_rows(self):
        """8.1/8.2: samma verktyg två gånger → två rader."""
        _persist_stream_tool_lines(
            self.env, self.session.id,
            self._loop([
                ('odoo_search', 'found 3 partners', 'call_a'),
                ('odoo_search', 'found 7 companies', 'call_b'),
            ]))

        lines = self._tool_lines()
        self.assertEqual(
            len(lines), 2,
            'B1: två anrop av samma verktyg måste ge två rader — '
            'dedupe på tool_name tappade den andra')
        self.assertEqual(
            sorted(lines.mapped('tool_call_id')), ['call_a', 'call_b'])

    def test_idempotent_on_rerun(self):
        """8.3: att köra persisteringen igen ska inte dubbla raderna."""
        loop = self._loop([
            ('odoo_search', 'found 3 partners', 'call_a'),
            ('odoo_read', 'partner 7', 'call_b'),
        ])
        _persist_stream_tool_lines(self.env, self.session.id, loop)
        first_count = len(self._tool_lines())
        _persist_stream_tool_lines(self.env, self.session.id, loop)
        self.assertEqual(
            len(self._tool_lines()), first_count,
            'persisteringen måste vara idempotent — en retry får inte '
            'dubbla verktygsspåret')

    def test_legacy_rows_without_call_id_dedupe_on_name(self):
        """Poster före 2026-10-08 saknar call_id — då gäller namnet."""
        self.env['ai.coworker.session.line'].create({
            'session_id': self.session.id,
            'role': 'tool',
            'tool_name': 'odoo_search',
            'tool_call_id': False,
            'content': 'legacy row',
            'sequence': 1,
        })
        _persist_stream_tool_lines(
            self.env, self.session.id,
            self._loop([('odoo_search', 'legacy row', '')]))

        self.assertEqual(
            len(self._tool_lines()), 1,
            'en gammal rad utan call_id ska fortfarande dedupas på namn')

    def test_new_call_id_alongside_legacy_row_is_written(self):
        """En ny rad med call_id får inte kollidera med en gammal namnrad."""
        self.env['ai.coworker.session.line'].create({
            'session_id': self.session.id,
            'role': 'tool',
            'tool_name': 'odoo_search',
            'tool_call_id': False,
            'content': 'legacy row',
            'sequence': 1,
        })
        _persist_stream_tool_lines(
            self.env, self.session.id,
            self._loop([('odoo_search', 'new result', 'call_new')]))

        self.assertEqual(
            len(self._tool_lines()), 2,
            'en ny rad med call_id är ett ANNAT anrop än den gamla raden')
