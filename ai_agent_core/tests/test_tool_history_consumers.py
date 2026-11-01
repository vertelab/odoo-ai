# -*- coding: utf-8 -*-
"""Konsumenterna av `tool_history` måste tåla tripler (A9).

Bakgrund: `core/loop.py` appenderar sedan 2026-10-08 TRE värden —
`(name, preview, tool_call_id)` — eftersom OpenAI-formatet kräver att en
tool-rad paras med assistant-radens `tool_calls[].id`. `tool_history_triples`
(core/loop.py:1193) är normaliseraren.

Buggen: `models/ai_coworker.py` packade upp två värden

    for t_name, t_preview in getattr(loop_obj, 'tool_history', []):

→ `ValueError: too many values to unpack (expected 2)` vid VARJE körning som
hade minst ett verktygsanrop. Följden var inte en krasch för användaren:
blocket låg i `try/except Exception: pass`, så `ai.coworker.error` fylldes
aldrig med `tool_error`/`search_error`/`max_rounds`/`provider_error`.
Fel-instrumenteringen var tyst död — verktygsfel försvann spårlöst.

Dessa tester bevakar att:
  1. normaliseraren ger tripler även för gamla 2-tuplar,
  2. ingen konsument utanför `core/loop.py` packar upp direkt,
  3. fel-loggningen faktiskt skapar rader (inte bara "kastar inte").
"""

import ast
import os

from odoo.tests import TransactionCase, tagged

MODULE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@tagged('ai_core', 'post_install', '-at_install')
class TestToolHistoryTriples(TransactionCase):
    """Normaliseraren är kontraktet — den ska vara tolerant, inte strikt."""

    def _triples(self, history):
        from odoo.addons.ai_agent_core.core.loop import tool_history_triples
        return tool_history_triples(history)

    def test_two_tuple_gets_empty_call_id(self):
        """Poster skrivna före 2026-10-08 har två värden — de får tom call_id."""
        out = self._triples([('odoo_search', 'found 3')])
        self.assertEqual(out, [('odoo_search', 'found 3', '')])

    def test_three_tuple_preserved(self):
        out = self._triples([('odoo_search', 'found 3', 'call_abc')])
        self.assertEqual(out, [('odoo_search', 'found 3', 'call_abc')])

    def test_mixed_history_normalises_to_same_shape(self):
        """Blandad historik (gammal + ny) ska ge enhetlig form."""
        out = self._triples([
            ('odoo_search', 'old row'),
            ('odoo_read', 'new row', 'call_xyz'),
        ])
        self.assertEqual(len(out), 2)
        for entry in out:
            self.assertEqual(len(entry), 3, 'varje post måste vara en trippel')

    def test_empty_and_none_are_safe(self):
        self.assertEqual(self._triples(None), [])
        self.assertEqual(self._triples([]), [])
        self.assertEqual(self._triples([None]), [])

    def test_missing_preview_does_not_crash(self):
        """En post med bara namn ska ge tom preview, inte IndexError."""
        out = self._triples([('odoo_search',)])
        self.assertEqual(out, [('odoo_search', '', '')])


@tagged('ai_core', 'post_install', '-at_install')
class TestNoDirectUnpacking(TransactionCase):
    """Strukturell grind: ingen konsument får packa upp `tool_history` direkt.

    Detta är testet som hade fångat A9 innan det nådde drift. Regeln är
    enkel: `tool_history` läses via `tool_history_triples`, punkt.
    `core/loop.py` undantas — det är där triplerna SKRIVS.
    """

    # Filer som får röra `tool_history` direkt.
    ALLOWED = {
        os.path.join('core', 'loop.py'),      # skrivaren + normaliseraren
    }

    def test_no_two_value_unpacking_outside_core_loop(self):
        offenders = []
        for dirpath, _dirnames, filenames in os.walk(MODULE_ROOT):
            if '__pycache__' in dirpath or os.sep + 'tests' in dirpath:
                continue
            for fname in filenames:
                if not fname.endswith('.py'):
                    continue
                path = os.path.join(dirpath, fname)
                rel = os.path.relpath(path, MODULE_ROOT)
                if rel in self.ALLOWED:
                    continue
                with open(path, encoding='utf-8') as fh:
                    try:
                        tree = ast.parse(fh.read())
                    except SyntaxError:
                        continue
                for node in ast.walk(tree):
                    if not isinstance(node, ast.For):
                        continue
                    # for a, b in <något som nämner tool_history>
                    src = ast.dump(node.iter)
                    if 'tool_history' not in src:
                        continue
                    if 'tool_history_triples' in src:
                        continue
                    if (isinstance(node.target, ast.Tuple)
                            and len(node.target.elts) == 2):
                        offenders.append(f'{rel}:{node.lineno}')

        self.assertEqual(
            offenders, [],
            'Direkt tvåvärdes-uppackning av tool_history — använd '
            'tool_history_triples() i stället: ' + ', '.join(offenders))


@tagged('ai_core', 'post_install', '-at_install')
class TestErrorLoggingSurvivesTriples(TransactionCase):
    """Fel-loggningen ska skapa rader även när historiken bär tripler.

    Testet går genom `ai.coworker.error`-modellen med samma villkor som
    loopen använder, så att klassificeringen (search_error vs tool_error)
    bevakas — inte bara att uppackningen slutar kasta.
    """

    def test_tool_error_row_created_from_triple_history(self):
        if 'ai.coworker.error' not in self.env:
            self.skipTest('ai.coworker.error saknas i denna installation')

        from odoo.addons.ai_agent_core.core.loop import tool_history_triples

        session = self.env['ai.coworker.session'].create({
            'name': 'A9 error logging test',
            'status': 'active',
        })
        history = [
            ('odoo_web_search', 'Search error: timeout', 'call_1'),
            ('odoo_read', 'Error: record not found', 'call_2'),
            ('odoo_search', 'found 3 records', 'call_3'),
        ]

        err_model = self.env['ai.coworker.error']
        created = 0
        for t_name, t_preview, _call_id in tool_history_triples(history):
            low = (t_preview or '').strip().lower()
            if low.startswith(('error:', 'search error:', 'fetch error:',
                               'no results found', 'nats ')):
                etype = ('search_error'
                         if t_name == 'odoo_web_search' else 'tool_error')
                err_model.create({
                    'session_id': session.id,
                    'error_type': etype,
                    'tool_name': t_name,
                    'message': str(t_preview)[:1000],
                })
                created += 1

        self.assertEqual(created, 2, 'två av tre rader är fel')
        rows = err_model.search([('session_id', '=', session.id)])
        self.assertEqual(len(rows), 2)
        types = sorted(rows.mapped('error_type'))
        self.assertEqual(types, ['search_error', 'tool_error'])
