# -*- coding: utf-8 -*-
"""Tester för Pi-session-koppling (session-cost-context 8.5).

Verifierar att:
  (a) ett nytt pi_session_id ALLTID skapar en ny session (ingen fallback
      till "närmast aktiva" — det skulle tappa 1:1-kopplingen),
  (b) samma pi_session_id återfinner samma session (idempotent),
  (c) en session som hittas via pi_session_id men saknar init_type
      självläks till 'openai_api',
  (d) fallbacken (närmast aktiva) endast används när inget pi_session_id
      skickas (icke-Pi-klienter),
  (e) `{pi session: <uuid>}`-markören plockas ur system-/user-text
      (transport C) — inkl. versaler och `pi_session`-varianter.
"""

from odoo.tests import common, tagged


@tagged('post_install')
class TestPiSessionLink(common.TransactionCase):
    """1:1-koppling Pi-session ↔ ai.coworker.session."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Session = cls.env['ai.coworker.session']
        cls.user = cls.env['res.users'].create({
            'name': 'Pi Link User',
            'login': 'pi_link@example.com',
            'email': 'pi_link@example.com',
        })
        cls.coworker = cls.env['ai.coworker'].create({
            'name': 'Pi Link Coworker',
            'description': 'Pi session link test',
            'status': 'active',
        })

    def _find_or_create(self, pi_session_id='', session_id=0, prompt=''):
        return self.Session._find_or_create_coworker_session(
            self.coworker.id, self.user.id,
            pi_session_id=pi_session_id, session_id=session_id,
            prompt=prompt)

    # ── (a) nytt id → ny session ──────────────────────────────────────

    def test_new_pi_session_creates_new_session(self):
        """Nytt pi_session_id skapar en NY session — aldrig fallback."""
        # Skapa en aktiv session först (fallback-kandidat).
        existing = self.Session.create({
            'coworker_id': self.coworker.id,
            'status': 'active',
            'user_id': self.user.id,
            'name': 'Existing active',
        })
        new_id = '11111111-2222-3333-4444-555555555555'
        sess, created = self._find_or_create(pi_session_id=new_id)
        self.assertTrue(created)
        self.assertNotEqual(sess.id, existing.id)
        self.assertEqual(sess.pi_session_id, new_id)
        self.assertEqual(sess.init_type, 'openai_api')

    # ── (b) samma id → samma session ──────────────────────────────────

    def test_same_pi_session_is_idempotent(self):
        """Samma pi_session_id återfinner samma session."""
        pid = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'
        first, created1 = self._find_or_create(pi_session_id=pid)
        second, created2 = self._find_or_create(pi_session_id=pid)
        self.assertTrue(created1)
        self.assertFalse(created2)
        self.assertEqual(first.id, second.id)

    # ── (c) självläkning av init_type ─────────────────────────────────

    def test_init_type_self_heals(self):
        """Session utan init_type som hittas via pi_session_id märks om."""
        pid = '99999999-8888-7777-6666-555555555555'
        sess = self.Session.create({
            'coworker_id': self.coworker.id,
            'status': 'active',
            'user_id': self.user.id,
            'name': 'No init type',
            'pi_session_id': pid,
        })
        self.assertFalse(sess.init_type)
        found, created = self._find_or_create(pi_session_id=pid)
        self.assertFalse(created)
        self.assertEqual(found.id, sess.id)
        self.assertEqual(found.init_type, 'openai_api')

    # ── (d) fallback endast utan pi_session_id ────────────────────────

    def test_fallback_only_without_pi_session_id(self):
        """Utan pi_session_id fortsätter närmast aktiva session."""
        existing = self.Session.create({
            'coworker_id': self.coworker.id,
            'status': 'active',
            'user_id': self.user.id,
            'name': 'Continue me',
        })
        sess, created = self._find_or_create(pi_session_id='')
        self.assertFalse(created)
        self.assertEqual(sess.id, existing.id)

    # ── (e) markör-extraktion (transport C) ───────────────────────────

    def test_extract_marker(self):
        """`{pi session: <uuid>}` plockas ur text (case/format-okänsligt)."""
        pid = '01a090e9-0709-75e6-9739-36f67d1b56ad'
        cases = [
            f'system\n\n{{pi session: {pid}}}',
            f'{{pi session:{pid}}}',
            f'{{pi_session: {pid}}}',
            f'{{PI SESSION : {pid.upper()}}}',
        ]
        for text in cases:
            self.assertEqual(
                self.Session._extract_pi_session_marker(text), pid,
                f'marker misslyckades för: {text!r}')

    def test_extract_marker_none(self):
        """Text utan markör → tom sträng."""
        self.assertEqual(
            self.Session._extract_pi_session_marker('ingen markör här'), '')
        self.assertEqual(
            self.Session._extract_pi_session_marker('', None), '')

    def test_extract_marker_first_wins(self):
        """Första markören i argumentordningen vinner."""
        pid1 = '11111111-1111-1111-1111-111111111111'
        pid2 = '22222222-2222-2222-2222-222222222222'
        self.assertEqual(
            self.Session._extract_pi_session_marker(
                f'{{pi session: {pid1}}}', f'{{pi session: {pid2}}}'),
            pid1)

    # ── (f) persistering: en line per messages[]-post (delta) ──────────

    def _pi_session(self, uuid):
        sess, _ = self.Session._find_or_create_coworker_session(
            20, self.env.user.id, pi_session_id=uuid, prompt='delta test')
        return sess

    def test_every_message_becomes_one_line(self):
        """Varje post i messages[] blir EXAKT en session line."""
        sess = self._pi_session('aaaaaaaa-0000-1111-2222-333333333333')
        msgs = [
            {'role': 'system', 'content': 'sys'},
            {'role': 'user', 'content': 'Hej!'},
            {'role': 'assistant', 'content': 'Hej själv!',
             'tool_calls': [{'id': 'c1', 'type': 'function',
                             'function': {'name': 'bash', 'arguments': '{}'}}]},
            {'role': 'tool', 'content': 'ok', 'tool_call_id': 'c1',
             'name': 'bash'},
            {'role': 'user', 'content': 'Kör det'},
        ]
        n = self.Session._persist_pi_messages(
            self.env, sess, msgs, input_t=100, model_real='')
        self.assertEqual(n, 5)
        lines = sess.session_line_ids.sorted('sequence')
        self.assertEqual(len(lines), 5)
        self.assertEqual(
            [l.role for l in lines],
            ['system', 'user', 'assistant', 'tool', 'user'])
        tool_line = lines.filtered(lambda l: l.role == 'tool')
        self.assertEqual(tool_line.tool_name, 'bash')
        self.assertEqual(tool_line.content, 'ok')
        # tool_calls sparas som JSON på assistant-raden
        asst = lines.filtered(lambda l: l.role == 'assistant')
        self.assertIn('bash', asst.tool_calls or '')
        # input-tokens läggs på sista NYA user-raden
        self.assertEqual(lines[-1].token_input, 100)
        self.assertEqual(sess.pi_message_count, 5)

    def test_delta_no_duplication(self):
        """Pi skickar hela historiken — bara NYA poster persisteras."""
        sess = self._pi_session('bbbbbbbb-0000-1111-2222-333333333333')
        msgs = [{'role': 'user', 'content': 'ett'}]
        self.Session._persist_pi_messages(self.env, sess, msgs, input_t=10)
        # andra anropet: samma historik + ett nytt meddelande
        msgs2 = msgs + [{'role': 'assistant', 'content': 'svar'},
                        {'role': 'user', 'content': 'två'}]
        n = self.Session._persist_pi_messages(
            self.env, sess, msgs2, input_t=20)
        self.assertEqual(n, 2)  # endast assistant + user (delta)
        self.assertEqual(len(sess.session_line_ids), 3)
        self.assertEqual(sess.pi_message_count, 3)

    def test_no_new_messages_is_noop(self):
        """Oförändrad historik → inga nya rader."""
        sess = self._pi_session('cccccccc-0000-1111-2222-333333333333')
        msgs = [{'role': 'user', 'content': 'ett'}]
        self.Session._persist_pi_messages(self.env, sess, msgs)
        n = self.Session._persist_pi_messages(self.env, sess, msgs)
        self.assertEqual(n, 0)
        self.assertEqual(len(sess.session_line_ids), 1)

    def test_dedup_direct_written_response(self):
        """Svaret som skrivs direkt vid förra anropet dubbleras inte."""
        sess = self._pi_session('dddddddd-0000-1111-2222-333333333333')
        msgs = [{'role': 'user', 'content': 'fråga'}]
        self.Session._persist_pi_messages(self.env, sess, msgs)
        # strömmen skriver svaret direkt (som stream.py gör)
        line = sess.session_line_ids
        self.env['ai.coworker.session.line'].create({
            'session_id': sess.id, 'role': 'assistant',
            'content': 'svaret', 'sequence': line[0].sequence + 1,
        })
        msgs2 = msgs + [{'role': 'assistant', 'content': 'svaret'},
                        {'role': 'user', 'content': 'ny fråga'}]
        n = self.Session._persist_pi_messages(self.env, sess, msgs2)
        self.assertEqual(n, 1)  # bara den nya user-raden
        assistants = sess.session_line_ids.filtered(
            lambda l: l.role == 'assistant')
        self.assertEqual(len(assistants), 1)
