# -*- coding: utf-8 -*-
"""Historiken måste para tool-rader mot sina anrop (fynd 2026-11-11).

VARFÖR DET HÄR TESTET FINNS: session 28097 svarade "(inget svar)" på varje
fråga, och orsaken var inte modellen utan historiken. Tool-raderna skrivs
NÄR VERKTYGET KÖRS — alltså före den assistant-rad som deklarerar anropen,
eftersom den raden skrivs först när hela turen är klar:

    seq 2–14   tool      (13 verktygssvar)
    seq 15     assistant (deklarerar tool_calls)

Historiken byggdes med `declared_call_ids` fylld av FÖREGÅENDE assistant-
rad — tom när tool-raderna lästes — och hoppade därför över varje tool-rad.
Providern fick ett assistant-meddelande med `tool_calls` och inga svar:

    400 invalid_request_error: An assistant message with 'tool_calls'
    must be followed by tool messages responding to each 'tool_call_id'.

Testet bevisar att en tool-rad som ligger FÖRE sitt anrop paras korrekt,
och att en verkligt föräldralös rad fortfarande utesluts.
"""

import json

from odoo.tests import common, tagged


@tagged('okf', 'session', 'post_install', '-at_install')
class TestHistoryToolPairing(common.TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Session = cls.env['ai.coworker.session']
        cls.Line = cls.env['ai.coworker.session.line']

    def _session(self, name='Historiktest'):
        return self.Session.create({
            'name': name,
            'coworker_id': self.env['ai.coworker'].create(
                {'name': 'Historiktest-coworker'}).id,
            'user_id': self.env.user.id,
        })

    def _line(self, session, seq, role, content='', **kw):
        vals = {
            'session_id': session.id,
            'sequence': seq,
            'role': role,
            'content': content,
        }
        vals.update(kw)
        return self.Line.create(vals)

    def _openai_calls(self, *call_ids):
        return json.dumps([
            {'id': cid, 'type': 'function',
             'function': {'name': 'odoo_search', 'arguments': '{}'}}
            for cid in call_ids
        ])

    # ── Ordningen som faktiskt råder ───────────────────────────────────

    def test_tool_lines_before_their_assistant_line_are_kept(self):
        """Tool-rader FÖRE assistant-raden — den verkliga ordningen."""
        s = self._session()
        self._line(s, 1, 'user', 'Lista 5 viktigaste mail')
        # Tool-svar skrivs först (de kördes först) ...
        self._line(s, 2, 'tool', '{"results": []}',
                   tool_call_id='call_a', tool_name='odoo_search')
        self._line(s, 3, 'tool', '{"model": "res.partner"}',
                   tool_call_id='call_b', tool_name='describe_model')
        # ... assistant-raden som deklarerar dem skrivs sist.
        self._line(s, 4, 'assistant', 'Klart.',
                   tool_calls=self._openai_calls('call_a', 'call_b'))

        history = s._build_history_from_lines()
        roles = [m.role.value for m in history]
        self.assertEqual(roles, ['user', 'tool', 'tool', 'assistant'],
                         'tool-raderna får inte tappas')
        tool_ids = [m.tool_call_id for m in history if m.role.value == 'tool']
        self.assertEqual(sorted(tool_ids), ['call_a', 'call_b'])

    def test_assistant_tool_calls_are_replayable(self):
        """Assistant-raden bär sina tool_calls i OpenAI-format."""
        s = self._session('Replay')
        self._line(s, 1, 'tool', '{}', tool_call_id='call_x',
                   tool_name='odoo_search')
        self._line(s, 2, 'assistant', 'x',
                   tool_calls=self._openai_calls('call_x'))
        history = s._build_history_from_lines()
        assistant = [m for m in history if m.role.value == 'assistant'][0]
        self.assertEqual(len(assistant.tool_calls), 1)
        self.assertEqual(assistant.tool_calls[0]['id'], 'call_x')

    def test_every_declared_call_has_a_response(self):
        """Ingen assistant-rad får lämna ett tool_call obesvarat.

        Det är exakt det providern avvisar med 400.
        """
        s = self._session('Parighet')
        self._line(s, 1, 'tool', '{}', tool_call_id='c1',
                   tool_name='odoo_search')
        self._line(s, 2, 'tool', '{}', tool_call_id='c2',
                   tool_name='okf_search')
        self._line(s, 3, 'assistant', 'svar',
                   tool_calls=self._openai_calls('c1', 'c2'))
        history = s._build_history_from_lines()
        declared = set()
        answered = set()
        for msg in history:
            if msg.role.value == 'assistant' and msg.tool_calls:
                declared.update(tc['id'] for tc in msg.tool_calls)
            elif msg.role.value == 'tool':
                answered.add(msg.tool_call_id)
        self.assertEqual(declared - answered, set(),
                         'obesvarade tool_calls ger 400 hos providern')

    def test_second_turn_keeps_its_tool_lines(self):
        """FLERA turer: en tidigare turs tool-rader får inte tappas.

        Fynd 2026-11-11 (samma session): efter att tool-raderna
        förregistrerats nollställdes mängden per assistant-rad — vilket
        raderade förregistreringen och tappade varje tool-rad som hörde
        till en TIDIGARE tur. Sessionen hade tre turer med verktyg; bara
        den första överlevde.
        """
        s = self._session('Flera turer')
        # Tur 1
        self._line(s, 1, 'user', 'fråga 1')
        self._line(s, 2, 'tool', '{}', tool_call_id='t1_a',
                   tool_name='odoo_search')
        self._line(s, 3, 'assistant', 'svar 1',
                   tool_calls=self._openai_calls('t1_a'))
        # Tur 2
        self._line(s, 4, 'user', 'fråga 2')
        self._line(s, 5, 'tool', '{}', tool_call_id='t2_a',
                   tool_name='describe_model')
        self._line(s, 6, 'tool', '{}', tool_call_id='t2_b',
                   tool_name='odoo_search')
        self._line(s, 7, 'assistant', 'svar 2',
                   tool_calls=self._openai_calls('t2_a', 't2_b'))

        history = s._build_history_from_lines()
        tool_ids = [m.tool_call_id for m in history
                    if m.role.value == 'tool']
        self.assertEqual(sorted(tool_ids), ['t1_a', 't2_a', 't2_b'],
                         'båda turernas tool-rader ska finnas')

        # Ingen assistant-rad får lämna ett tool_call obesvarat.
        declared, answered = set(), set()
        for msg in history:
            if msg.role.value == 'assistant' and msg.tool_calls:
                declared.update(tc['id'] for tc in msg.tool_calls)
            elif msg.role.value == 'tool':
                answered.add(msg.tool_call_id)
        self.assertEqual(declared - answered, set())

    # ── Säkerhetsnätet är kvar ────────────────────────────────────────

    def test_truly_orphan_tool_line_is_dropped(self):
        """En tool-rad vars id ALDRIG deklareras ska fortfarande uteslutas."""
        s = self._session('Orphan')
        self._line(s, 1, 'tool', '{}', tool_call_id='call_orphan',
                   tool_name='odoo_search')
        self._line(s, 2, 'assistant', 'svar',
                   tool_calls=self._openai_calls('call_other'))
        history = s._build_history_from_lines()
        tool_ids = [m.tool_call_id for m in history if m.role.value == 'tool']
        self.assertNotIn('call_orphan', tool_ids,
                         'föräldralös rad får inte skickas')

    def test_tool_line_without_id_is_dropped(self):
        s = self._session('NoId')
        self._line(s, 1, 'tool', '{}', tool_name='odoo_search')
        self._line(s, 2, 'assistant', 'svar')
        history = s._build_history_from_lines()
        self.assertEqual(
            [m.role.value for m in history], ['assistant'])

    def test_preview_format_does_not_create_tool_lines(self):
        """Preview-formatet har inga id:n — det blir en sammanfattning."""
        s = self._session('Preview')
        preview = json.dumps([{'name': 'odoo_search', 'preview': '5 rader'}])
        self._line(s, 1, 'assistant', 'x', tool_calls=preview)
        history = s._build_history_from_lines()
        self.assertEqual(len(history), 1)
        self.assertIn('odoo_search', history[0].content)
        self.assertFalse(history[0].tool_calls)
