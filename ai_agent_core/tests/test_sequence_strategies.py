# -*- coding: utf-8 -*-
"""B9/B10 — sekvensskrivningar i samma tur.

Bakgrund: en enda tur skrev sina rader med TRE olika sekvensstrategier:

  * `_base_seq`, `_base_seq + 1`, `_base_seq + 2 + i` — korrekt (user,
    assistant, verktygsrader),
  * `_base_seq + 2 + len(tool_history)` — statistikraden (B9),
  * `len(session.session_line_ids) + 1` — `agent_usage`-raderna (B10).

Den sista är den värsta: den räknar RADER, inte sekvenser. Så snart
verktygsrader skrivits ligger radantalet efter sekvensvärdet, och
`agent_usage`-raden kolliderar med en befintlig sekvens. Med constrainten
från B2 blir det ett fel; utan den blev ordningen godtycklig.

Det här testet kör en realistisk tur (user + assistant + 2 verktyg +
agent_usage + statistikrad) och kräver att alla sekvenser är skilda och i
skapelseordning.
"""

from odoo.tests.common import TransactionCase, tagged


@tagged('ai_core', 'post_install', '-at_install')
class TestSequenceStrategiesAgree(TransactionCase):
    """15.1/15.4: sex rader i samma tur ska ge sex skilda sekvenser."""

    def setUp(self):
        super().setUp()
        self.coworker = self.env['ai.coworker'].create({
            'name': 'B9 Coworker',
        })
        self.session = self.env['ai.coworker.session'].create({
            'coworker_id': self.coworker.id,
            'name': 'B9 session',
            'status': 'active',
            'user_id': self.env.user.id,
        })
        self.Line = self.env['ai.coworker.session.line']

    def test_all_strategies_produce_distinct_sequences(self):
        """Simulera tur-ordningen: user, assistant, 2 tools, usage, statistik.

        Vi använder de FAKTISKA hjälparna (`_next_session_sequence`) för de
        rader som ska använda dem, och verifierar att resultatet är unikt.
        """
        from odoo.addons.ai_agent_core.controllers.stream import (
            _next_session_sequence)

        seqs = []

        def add(role, **kw):
            seq = _next_session_sequence(self.env, self.session.id)
            self.Line.create(dict(
                session_id=self.session.id, role=role,
                content=kw.pop('content', role), sequence=seq, **kw))
            seqs.append(seq)
            return seq

        add('user', content='fråga')
        add('assistant', content='svar')
        add('tool', tool_name='odoo_search', content='found 3')
        add('tool', tool_name='odoo_read', content='partner 7')
        add('system', content='agent_usage: 120 tokens')
        add('system', content='Verktygsselektion: 2 turer')

        self.assertEqual(len(seqs), 6)
        self.assertEqual(
            len(set(seqs)), 6,
            'B9/B10: sex rader i samma tur fick kolliderande sekvenser: %s'
            % seqs)
        self.assertEqual(
            seqs, sorted(seqs),
            'sekvenserna ska vara i skapelseordning: %s' % seqs)

    def test_row_count_is_not_sequence(self):
        """B10: `len(session_line_ids) + 1` är fel när sekvenser hoppar."""
        from odoo.addons.ai_agent_core.controllers.stream import (
            _next_session_sequence)

        # Skriv en rad med hög sekvens — radantalet blir då 1, sekvensen 10.
        self.Line.create({
            'session_id': self.session.id, 'role': 'user',
            'content': 'a', 'sequence': 10,
        })
        self.assertEqual(len(self.session.session_line_ids), 1)
        self.assertEqual(
            _next_session_sequence(self.env, self.session.id), 11,
            'nästa sekvens ska följa MAX, inte radantalet')
        self.assertNotEqual(
            len(self.session.session_line_ids) + 1,
            _next_session_sequence(self.env, self.session.id),
            'B10: radantal+1 sammanfaller inte med nästa sekvens här — '
            'det är precis därför strategierna inte får blandas')

    def test_no_raw_len_sequence_in_coworker(self):
        """Strukturell grind: `len(...session_line_ids) + 1` som sekvens."""
        import ast
        import os
        from odoo.addons.ai_agent_core import __file__ as pkg_file

        path = os.path.join(os.path.dirname(pkg_file), 'models',
                            'ai_coworker.py')
        with open(path, encoding='utf-8') as fh:
            src = fh.read()

        offenders = []
        for node in ast.walk(ast.parse(src)):
            # Leta dict-literaler med 'sequence': <något som räknar rader>
            if not isinstance(node, ast.Dict):
                continue
            for key, value in zip(node.keys, node.values):
                if not (isinstance(key, ast.Constant)
                        and key.value == 'sequence'):
                    continue
                seg = ast.get_source_segment(src, value) or ''
                if 'session_line_ids' in seg or 'len(' in seg:
                    offenders.append((node.lineno, seg))

        self.assertEqual(
            offenders, [],
            'B9/B10: sekvensen får inte räknas ur radantal — använd '
            '_next_session_sequence: %s' % offenders)

    def test_no_hardcoded_sequence_offset(self):
        """B9: statistikraden får inte använda `_base_seq + 2 + len(...)`."""
        import ast
        import os
        from odoo.addons.ai_agent_core import __file__ as pkg_file

        path = os.path.join(os.path.dirname(pkg_file), 'models',
                            'ai_coworker.py')
        with open(path, encoding='utf-8') as fh:
            src = fh.read()

        offenders = []
        for node in ast.walk(ast.parse(src)):
            if not isinstance(node, ast.Dict):
                continue
            for key, value in zip(node.keys, node.values):
                if not (isinstance(key, ast.Constant)
                        and key.value == 'sequence'):
                    continue
                seg = ast.get_source_segment(src, value) or ''
                if 'tool_history' in seg and 'len(' in seg:
                    offenders.append((node.lineno, seg))

        self.assertEqual(
            offenders, [],
            'B9: statistikradens sekvens får inte härledas ur '
            'len(tool_history): %s' % offenders)
