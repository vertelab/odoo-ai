# -*- coding: utf-8 -*-
"""Unik sekvens per session (B2).

Bakgrund: `ai_coworker_session_line` saknade unikhetsvillkor på
`(session_id, sequence)`. Sekvensen sätts av `_next_session_sequence`, som
läser max och skriver max+1 — en läs-och-skriv-cykel utan lås. Två samtidiga
skrivningar i samma session fick därför samma sekvens. Konsekvensen är inte
ett kraschat anrop utan en tyst felaktig historik: `_build_history_from_lines`
parar assistant-`tool_calls` med tool-rader, och med två rader på samma
sekvens blir ordningen godtycklig — fel verktygsspår kan paras med fel svar.

Migrationen i `migrations/18.0.1.334` städar befintliga dubbletter FÖRE
constrainten läggs på.
"""

from odoo.exceptions import ValidationError
from odoo.tests.common import TransactionCase, tagged
from psycopg2 import IntegrityError


@tagged('ai_core', 'post_install', '-at_install')
class TestSessionLineSequenceUnique(TransactionCase):
    """9.3: två rader med samma (session_id, sequence) — den andra avvisas."""

    def setUp(self):
        super().setUp()
        self.coworker = self.env['ai.coworker'].create({
            'name': 'Sequence Unique Test',
        })
        self.session = self.env['ai.coworker.session'].create({
            'coworker_id': self.coworker.id,
            'name': 'Seq session',
            'status': 'active',
            'user_id': self.env.user.id,
        })
        self.Line = self.env['ai.coworker.session.line']

    def _line(self, sequence, content='x'):
        return self.Line.create({
            'session_id': self.session.id,
            'role': 'user',
            'content': content,
            'sequence': sequence,
        })

    def test_duplicate_sequence_rejected(self):
        self._line(1)
        with self.assertRaises(IntegrityError):
            with self.env.cr.savepoint():
                self._line(1, 'duplicate')

    def test_same_sequence_in_different_sessions_allowed(self):
        """Constrainten gäller PER SESSION — inte globalt."""
        other = self.env['ai.coworker.session'].create({
            'coworker_id': self.coworker.id,
            'name': 'Other session',
            'status': 'active',
            'user_id': self.env.user.id,
        })
        self._line(1)
        self.Line.create({
            'session_id': other.id,
            'role': 'user',
            'content': 'same sequence, other session',
            'sequence': 1,
        })
        self.assertEqual(
            self.Line.search_count([('sequence', '=', 1)]), 2)

    def test_constraint_is_declared(self):
        """Strukturell grind: constrainten får inte tas bort tyst."""
        names = [c[0] for c in self.Line._sql_constraints]
        self.assertIn(
            'session_sequence_uniq', names,
            'den unika constrainten på (session_id, sequence) saknas')


@tagged('ai_core', 'post_install', '-at_install')
class TestNormalPersistAfterConstraint(TransactionCase):
    """9.4: normal persistering ska fortfarande fungera."""

    def setUp(self):
        super().setUp()
        self.coworker = self.env['ai.coworker'].create({
            'name': 'Normal Persist Test',
        })
        self.session = self.env['ai.coworker.session'].create({
            'coworker_id': self.coworker.id,
            'name': 'Persist session',
            'status': 'active',
            'user_id': self.env.user.id,
        })

    def test_user_assistant_tool_sequence_is_contiguous(self):
        """user → tool → tool → assistant ska ge 1,2,3,4 utan kollision."""
        from odoo.addons.ai_agent_core.controllers.stream import (
            _next_session_sequence, _persist_stream_tool_lines)

        Line = self.env['ai.coworker.session.line']
        Line.create({
            'session_id': self.session.id, 'role': 'user',
            'content': 'hej', 'sequence': _next_session_sequence(
                self.env, self.session.id),
        })

        class _Loop:
            tool_history = [
                ('odoo_search', 'found 3', 'call_1'),
                ('odoo_read', 'partner 7', 'call_2'),
            ]

        _persist_stream_tool_lines(self.env, self.session.id, _Loop())
        Line.create({
            'session_id': self.session.id, 'role': 'assistant',
            'content': 'svar', 'sequence': _next_session_sequence(
                self.env, self.session.id),
        })

        sequences = Line.search([
            ('session_id', '=', self.session.id),
        ]).sorted('sequence').mapped('sequence')
        self.assertEqual(sequences, [1, 2, 3, 4])
        self.assertEqual(len(sequences), len(set(sequences)),
                         'sekvenserna måste vara unika')

    def test_next_sequence_is_max_plus_one(self):
        from odoo.addons.ai_agent_core.controllers.stream import (
            _next_session_sequence)

        Line = self.env['ai.coworker.session.line']
        Line.create({
            'session_id': self.session.id, 'role': 'user',
            'content': 'a', 'sequence': 5,
        })
        Line.create({
            'session_id': self.session.id, 'role': 'user',
            'content': 'b', 'sequence': 9,
        })
        self.assertEqual(
            _next_session_sequence(self.env, self.session.id), 10,
            'nästa sekvens ska härledas från MAX, inte från radantalet')
