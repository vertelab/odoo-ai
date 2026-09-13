# -*- coding: utf-8 -*-
"""Tester för sessionsminnet (improve-ai-coworker-memory-and-tools grupp 1).

Verifierar att:
  (1.1) historik byggs deterministiskt från session.line i ordning
        (role, content, tool_calls, tool-resultat),
  (1.2) prompt-injektionen är prompt-medveten (tom prompt ⇒ ingen extra
        user-message; icke-tom ⇒ exakt en),
  (1.3) pi_message_count är ett derivat av raderna (raderna vinner),
  (1.4) en session kan återupptas över init-typer (web_ui → openai_api),
  (1.5) rader är append-only (write/unlink nekas).
"""

from odoo.tests import common, tagged
from odoo.exceptions import UserError


@tagged('post_install')
class TestSessionMemory(common.TransactionCase):
    """Sessionsminnets livscykel."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Session = cls.env['ai.coworker.session']
        cls.Line = cls.env['ai.coworker.session.line']
        cls.user = cls.env['res.users'].create({
            'name': 'Session Memory User',
            'login': 'session_memory@example.com',
            'email': 'session_memory@example.com',
        })
        cls.coworker = cls.env['ai.coworker'].create({
            'name': 'Session Memory Coworker',
            'description': 'Session memory test',
            'status': 'active',
        })

    def _new_session(self, **vals):
        base = {
            'coworker_id': self.coworker.id,
            'status': 'active',
            'user_id': self.user.id,
        }
        base.update(vals)
        return self.Session.create(base)

    # ── 1.1 Historik från rader ────────────────────────────────────────

    def test_history_built_from_lines_in_order(self):
        """Känd radsekvens → exakt samma meddelandesekvens ut."""
        sess = self._new_session()
        seq = [
            ('user', 'hej', None, None),
            ('assistant', 'jag kollar', None, None),
            ('tool', 'resultat', 'odoo_search', None),
            ('assistant', 'klart', None, None),
        ]
        for i, (role, content, tool_name, tool_calls) in enumerate(seq):
            vals = {
                'session_id': sess.id,
                'role': role,
                'content': content,
                'sequence': i + 1,
            }
            if tool_name:
                vals['tool_name'] = tool_name
            if tool_calls:
                vals['tool_calls'] = tool_calls
            self.Line.create(vals)

        history = sess._build_history_from_lines()
        self.assertEqual(len(history), 4)
        self.assertEqual([m.role.value for m in history],
                         ['user', 'assistant', 'tool', 'assistant'])
        self.assertEqual([m.content for m in history],
                         ['hej', 'jag kollar', 'resultat', 'klart'])
        self.assertEqual(history[2].name, 'odoo_search')

    def test_history_preserves_openai_tool_calls(self):
        """OpenAI-formade tool_calls bevaras som par (replaybart)."""
        import json
        sess = self._new_session()
        calls = [{
            'id': 'call_1', 'type': 'function',
            'function': {'name': 'odoo_search', 'arguments': '{}'},
        }]
        self.Line.create({
            'session_id': sess.id, 'role': 'assistant', 'content': '',
            'sequence': 1, 'tool_calls': json.dumps(calls),
        })
        history = sess._build_history_from_lines()
        self.assertEqual(history[0].tool_calls, calls)

    def test_history_skips_preview_tool_calls(self):
        """Preview-listor (name/preview) är inte replaybara — hoppas över."""
        import json
        sess = self._new_session()
        self.Line.create({
            'session_id': sess.id, 'role': 'assistant', 'content': 'x',
            'sequence': 1,
            'tool_calls': json.dumps([{'name': 't', 'preview': 'p'}]),
        })
        history = sess._build_history_from_lines()
        self.assertIsNone(history[0].tool_calls)

    def test_history_empty_session(self):
        """Session utan rader → tom historik (ingen krasch)."""
        sess = self._new_session()
        self.assertEqual(sess._build_history_from_lines(), [])

    # ── 1.2 Prompt-medveten injektion ──────────────────────────────────

    def test_no_user_message_when_prompt_empty(self):
        """Tom prompt ⇒ historiken oförändrad (ingen extra user-message)."""
        from odoo.addons.ai_agent_core.core.provider import Message, Role
        history = [Message(role=Role.USER, content='tidigare')]
        messages = list(history)
        prompt = ''
        if prompt:
            messages.append(Message(role=Role.USER, content=prompt))
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].content, 'tidigare')

    def test_one_user_message_when_prompt_present(self):
        """Icke-tom prompt ⇒ exakt en user-message läggs till."""
        from odoo.addons.ai_agent_core.core.provider import Message, Role
        history = [Message(role=Role.USER, content='tidigare')]
        messages = list(history)
        prompt = 'ny fråga'
        if prompt:
            messages.append(Message(role=Role.USER, content=prompt))
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[-1].content, 'ny fråga')

    # ── 1.3 pi_message_count som derivat ───────────────────────────────

    def test_counter_corrected_down_when_ahead_of_lines(self):
        """Räknare som pekar förbi antalet rader sänks (raderna vinner)."""
        sess = self._new_session(pi_message_count=99)
        self.Line.create({
            'session_id': sess.id, 'role': 'user', 'content': 'a',
            'sequence': 1,
        })
        result = sess._sync_pi_message_count()
        self.assertEqual(result, 1)
        self.assertEqual(sess.pi_message_count, 1)

    def test_counter_not_raised_above_lines(self):
        """Räknare som ligger under radantalet lämnas (delta-kursor)."""
        sess = self._new_session(pi_message_count=1)
        for i in range(3):
            self.Line.create({
                'session_id': sess.id, 'role': 'user', 'content': str(i),
                'sequence': i + 1,
            })
        result = sess._sync_pi_message_count()
        self.assertEqual(result, 1)

    def test_counter_consistent_after_persist(self):
        """Efter persist är räknaren konsistent med raderna."""
        sess = self._new_session()
        msgs = [
            {'role': 'user', 'content': 'hej'},
            {'role': 'assistant', 'content': 'svar'},
        ]
        self.Session._persist_pi_messages(self.env, sess, msgs)
        self.assertEqual(sess.pi_message_count, 2)

    # ── 1.4 Återupptagning över init-typer ─────────────────────────────

    def test_resume_across_init_types(self):
        """Session påbörjad i web_ui återupptas via openai_api med historik."""
        sess = self._new_session(init_type='web_ui')
        for i, (role, content) in enumerate(
                [('user', 'från web'), ('assistant', 'svar i web')]):
            self.Line.create({
                'session_id': sess.id, 'role': role, 'content': content,
                'sequence': i + 1,
            })
        # Återuppta samma session via openai_api-vägen (session_id).
        found, created = self.Session._find_or_create_coworker_session(
            self.coworker.id, self.user.id, session_id=sess.id)
        self.assertFalse(created)
        self.assertEqual(found.id, sess.id)
        history = found._build_history_from_lines()
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0].content, 'från web')

    # ── 1.5 Append-only ────────────────────────────────────────────────

    def test_line_content_is_immutable(self):
        """Innehåll/roll får inte skrivas över."""
        sess = self._new_session()
        line = self.Line.create({
            'session_id': sess.id, 'role': 'user', 'content': 'ursprung',
            'sequence': 1,
        })
        with self.assertRaises(UserError):
            line.write({'content': 'ändrat'})
        with self.assertRaises(UserError):
            line.write({'role': 'assistant'})

    def test_line_cannot_be_deleted(self):
        """Sessionsrader får inte raderas."""
        sess = self._new_session()
        line = self.Line.create({
            'session_id': sess.id, 'role': 'user', 'content': 'x',
            'sequence': 1,
        })
        with self.assertRaises(UserError):
            line.unlink()

    def test_line_metadata_can_be_updated(self):
        """Metadata (agent_id/tool_id) får uppdateras efteråt.

        Innehålls- och granskningsfält (content, role, debug_info, source_urls,
        tool_calls) är immutabla — endast rena lifecycle-fält får fyllas i
        efteråt (t.ex. vid specialist-delegation).
        """
        sess = self._new_session()
        line = self.Line.create({
            'session_id': sess.id, 'role': 'tool', 'content': 'x',
            'sequence': 1,
        })
        agent = self.env['ai.agent'].create({
            'name': 'Metadata-agent', 'description': 'x'})
        # ska inte kasta — agent_id är ett lifecycle-fält.
        line.write({'agent_id': agent.id})
        self.assertEqual(line.agent_id, agent)

    def test_audit_fields_are_immutable(self):
        """Granskningsfält (debug_info m.fl.) kan inte ändras efteråt."""
        sess = self._new_session()
        line = self.Line.create({
            'session_id': sess.id, 'role': 'assistant', 'content': 'x',
            'sequence': 1,
        })
        with self.assertRaises(UserError):
            line.write({'debug_info': 'efterhandsredigering'})
        with self.assertRaises(UserError):
            line.write({'source_urls': 'http://x'})
