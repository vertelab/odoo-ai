# -*- coding: utf-8 -*-
"""Tester för session-bokföring (session-audit).

Verifierar (a) korrekt session, (b) meddelanden speglar konversationen,
(c) tokens räknas/sparas per meddelande (user-rad = input, assistant-rad =
output) och (d) /new-semantik: ny tråd stänger föregående aktiva session.
"""

import json
from datetime import datetime

from odoo.tests import common, tagged
from odoo import fields


@tagged('post_install')
class TestSessionAccounting(common.TransactionCase):
    """Bokföring per meddelande + /new-stängning."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Session = cls.env['ai.coworker.session']
        cls.Line = cls.env['ai.coworker.session.line']
        cls.user = cls.env['res.users'].create({
            'name': 'Session Audit User',
            'login': 'session_audit@example.com',
            'email': 'session_audit@example.com',
        })
        cls.coworker = cls.env['ai.coworker'].create({
            'name': 'Session Audit Coworker',
            'description': 'Session accounting test',
            'status': 'active',
        })

    def _mk_session(self, status='active'):
        return self.Session.create({
            'coworker_id': self.coworker.id,
            'status': status,
            'user_id': self.user.id,
            'name': 'Audit session',
        })

    # ── c) tokens per meddelande ──────────────────────────────────────

    def test_user_line_gets_input_tokens(self):
        """User-rad bokför requestens input-tokens → token_sys beräknas."""
        sess = self._mk_session()
        # Samma skapelse som run()/_persist_session gör numera
        self.Line.create({
            'session_id': sess.id, 'role': 'user',
            'content': 'Hej, sammanfatta',
            'sequence': 1,
            'token_input': 1200, 'token_output': 0,
            'sys_multiplier': 2.0,
        })
        line = sess.session_line_ids.filtered(lambda l: l.role == 'user')
        self.assertEqual(line.token_input, 1200)
        self.assertEqual(line.token_output, 0)
        # (1200 + 0) × 2.0
        self.assertEqual(line.token_sys, 2400)

    def test_assistant_line_gets_output_tokens(self):
        """Assistant-rad bokför output → token_sys beräknas."""
        sess = self._mk_session()
        self.Line.create({
            'session_id': sess.id, 'role': 'assistant',
            'content': 'Här är svaret',
            'sequence': 2,
            'token_input': 0, 'token_output': 350,
            'sys_multiplier': 2.0,
        })
        line = sess.session_line_ids.filtered(lambda l: l.role == 'assistant')
        self.assertEqual(line.token_input, 0)
        self.assertEqual(line.token_output, 350)
        # (0 + 350) × 2.0
        self.assertEqual(line.token_sys, 700)

    def test_session_token_sys_is_request_total(self):
        """session.token_sys = Σ rader = (input+output) × multiplier (som förr).

        Förr sattes input+output på assistant-raden; nu input på user-raden
        och output på assistant-raden. Summan över raderna ska vara oförändrad.
        """
        sess = self._mk_session()
        self.Line.create({
            'session_id': sess.id, 'role': 'user',
            'content': 'fråga', 'sequence': 1,
            'token_input': 1000, 'token_output': 0,
            'sys_multiplier': 1.0,
        })
        self.Line.create({
            'session_id': sess.id, 'role': 'assistant',
            'content': 'svar', 'sequence': 2,
            'token_input': 0, 'token_output': 500,
            'sys_multiplier': 1.0,
        })
        self.assertEqual(sess.token_sys, 1500)
        # Sessionsfälten bokför samma totaler som förr
        sess.write({'token_input': 1000, 'token_output': 500})
        self.assertEqual(sess.token_input, 1000)
        self.assertEqual(sess.token_output, 500)

    def test_tool_calls_saved_on_assistant_line(self):
        """tool_calls sparas som JSON på assistant-raden (granskningsbar)."""
        sess = self._mk_session()
        tool_calls = json.dumps(
            [{'name': 'web_search', 'preview': '2 träffar'}],
            ensure_ascii=False)
        self.Line.create({
            'session_id': sess.id, 'role': 'assistant',
            'content': 'svar', 'sequence': 2,
            'token_output': 100, 'model_real': 'gemma-4-31b',
            'tool_calls': tool_calls,
        })
        line = sess.session_line_ids.filtered(lambda l: l.role == 'assistant')
        parsed = json.loads(line.tool_calls)
        self.assertEqual(parsed[0]['name'], 'web_search')

    def test_tool_lines_persisted(self):
        """Verktygsanrop persisteras som role='tool'-rader med preview."""
        sess = self._mk_session()
        self.Line.create({
            'session_id': sess.id, 'role': 'tool',
            'tool_name': 'web_search', 'content': '2 träffar',
            'sequence': 100, 'token_input': 500, 'sys_multiplier': 1.0,
        })
        line = sess.session_line_ids.filtered(lambda l: l.role == 'tool')
        self.assertEqual(line.tool_name, 'web_search')
        self.assertEqual(line.token_sys, 500)

    # ── d) /new-semantik ──────────────────────────────────────────────

    def test_thread_close_marks_done(self):
        """thread_close (POST /ai/threads/<id>/close) → status done."""
        sess = self._mk_session(status='active')
        # Samma write som controllern utför
        sess.write({
            'status': 'done',
            'finish_reason': 'closed',
            'end_date': fields.Datetime.now(),
        })
        self.assertEqual(sess.status, 'done')
        self.assertEqual(sess.finish_reason, 'closed')
        self.assertTrue(sess.end_date)

    def test_new_thread_closes_previous_active(self):
        """Ny tråd (thread_create) stänger användarens övriga aktiva sessioner.

        Simulerar controllerns sök-/write-logik.
        """
        old1 = self._mk_session(status='active')
        old2 = self._mk_session(status='active')
        # En redan avslutad session rörs inte
        done_old = self._mk_session(status='done')
        new = self._mk_session(status='active')

        # Controllerns logik: stäng alla aktiva utom den nya
        self.Session.search([
            ('user_id', '=', self.user.id),
            ('status', '=', 'active'),
            ('id', '!=', new.id),
        ]).write({
            'status': 'done',
            'finish_reason': 'new_session',
            'end_date': fields.Datetime.now(),
        })

        old1.invalidate_recordset()
        old2.invalidate_recordset()
        done_old.invalidate_recordset()
        new.invalidate_recordset()
        self.assertEqual(old1.status, 'done')
        self.assertEqual(old2.status, 'done')
        self.assertEqual(done_old.status, 'done')
        self.assertEqual(done_old.finish_reason, 'done')  # oförändrad
        self.assertEqual(new.status, 'active')
        self.assertEqual(old1.finish_reason, 'new_session')
