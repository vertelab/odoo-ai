# -*- coding: utf-8 -*-
"""pi-message-persist-dedup: samma Pi-post ger en rad, retry-anrop bevaras.

VARFÖR: Pi skickar hela sin `messages[]` vid varje anrop. Dedupen byggde på
kursorn `pi_message_count`, som antar att listan växer monotont. Pi
kompakterar historiken — då är listan kortare, kursorn pekar förbi, och hela
historiken skrevs om (mätt: 115 omskrivningar av samma prompt i en session,
224 985 identiska rader i drift).

Dessa tester bevisar:
  1. samma post två gånger → en rad
  2. kortare historik (kompaktering) → bara de nya posterna skrivs
  3. retry-anrop (samma text, olika tokens) → bevaras som egna rader
  4. samma text i olika roller → olika poster
  5. två tool-poster med olika tool_call_id → olika poster
"""

from odoo.tests.common import TransactionCase, tagged


@tagged('pi_message_dedup', 'post_install', '-at_install')
class TestPiMessageDedup(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Session = cls.env['ai.coworker.session']
        cls.Line = cls.env['ai.coworker.session.line']
        cls.user = cls.env['res.users'].create({
            'name': 'Dedup User',
            'login': 'dedup@example.com',
            'email': 'dedup@example.com',
        })
        cls.coworker = cls.env['ai.coworker'].create({
            'name': 'Dedup Coworker',
            'status': 'active',
        })

    def _session(self):
        return self.Session.create({
            'coworker_id': self.coworker.id,
            'status': 'active',
            'user_id': self.user.id,
            'name': 'dedup',
        })

    def _persist(self, session, messages, input_t=0, output_t=0):
        return self.Session._persist_pi_messages(
            self.env, session, messages,
            input_t=input_t, output_t=output_t)

    def _lines(self, session):
        return self.Line.search([('session_id', '=', session.id)])

    # ── 1. samma post två gånger ───────────────────────────────────────

    def test_same_post_twice_gives_one_line(self):
        """Samma post i två anrop → en rad."""
        session = self._session()
        msgs = [
            {'role': 'user', 'content': 'Hej'},
            {'role': 'assistant', 'content': 'Hej själv'},
        ]
        self._persist(session, msgs)
        self.assertEqual(len(self._lines(session)), 2)

        # Pi skickar HELA historiken igen (monotont växande — kursorn gäller).
        self._persist(session, msgs)
        self.assertEqual(len(self._lines(session)), 2,
                         'ingen ny rad för en post som redan finns')

        # Även vid ogiltig kursor (kompaktering) ska samma post dedupas.
        session.write({'pi_message_count': 99})
        self._persist(session, msgs)
        self.assertEqual(len(self._lines(session)), 2)

    # ── 2. kompaktering ────────────────────────────────────────────────

    def test_shorter_history_does_not_rewrite(self):
        """Kompaktering: 500 poster → 12 poster varav 10 nya → 10 nya rader."""
        session = self._session()
        old = [{'role': 'user', 'content': 'gammal %s' % i}
               for i in range(500)]
        self._persist(session, old)
        self.assertEqual(len(self._lines(session)), 500)

        summary = {
            'role': 'user',
            'content': 'The conversation history before this point was '
                       'compacted into the following summary:\n\n<summary>…',
        }
        new = [summary] + [
            {'role': 'user', 'content': 'ny %s' % i} for i in range(9)]
        self._persist(session, new)

        self.assertEqual(len(self._lines(session)), 510,
                         '500 gamla + 10 nya, inte 500 + 12')
        self.assertEqual(
            self.Line.search_count([
                ('session_id', '=', session.id),
                ('content', '=', 'gammal 0'),
            ]), 1, 'den gamla historiken skrivs inte om')

    def test_summary_written_once(self):
        """Kompakteringssammanfattningen skrivs en gång."""
        session = self._session()
        summary = {
            'role': 'user',
            'content': 'The conversation history before this point was '
                       'compacted into the following summary: X',
        }
        self._persist(session, [summary])
        session.write({'pi_message_count': 99})   # ogiltig kursor
        self._persist(session, [summary])
        self.assertEqual(
            self.Line.search_count([
                ('session_id', '=', session.id),
                ('role', '=', 'user'),
            ]), 1)

    # ── 3. retry-anrop bevaras ─────────────────────────────────────────

    def test_retry_calls_are_preserved(self):
        """Samma text, olika tokenantal = separata anrop (Pi:s retry-loop).

        Vid ogiltig kursor (kompaktering) filtreras bara poster som redan
        finns bort — en post som finns men bär ett annat tokenantal är ett
        retry-anrop och ska bevaras.
        """
        session = self._session()
        msg = [{'role': 'user', 'content': 'samma prompt'}]
        self._persist(session, msg, input_t=98_066)
        # Tvinga ogiltig kursor: kursorn ligger över antalet poster.
        session.write({'pi_message_count': 99})
        self._persist(session, msg, input_t=98_325)
        session.write({'pi_message_count': 99})
        self._persist(session, msg, input_t=98_557)

        lines = self._lines(session)
        self.assertEqual(len(lines), 3,
                         'tre anrop med olika tokens är tre rader')
        self.assertEqual(sorted(lines.mapped('token_input')),
                         [98_066, 98_325, 98_557])

    def test_identical_tokens_are_deduped(self):
        """Exakt samma post igen (ogiltig kursor) → dedupas."""
        session = self._session()
        msg = [{'role': 'user', 'content': 'samma prompt'}]
        self._persist(session, msg, input_t=1_000)
        session.write({'pi_message_count': 99})   # ogiltig kursor
        self._persist(session, msg, input_t=1_000)
        self.assertEqual(len(self._lines(session)), 1)

    # ── 4. roll skiljer ────────────────────────────────────────────────

    def test_same_text_different_role(self):
        """Samma text som user och assistant → två rader."""
        session = self._session()
        self._persist(session, [{'role': 'user', 'content': 'Hej'}])
        session.write({'pi_message_count': 99})   # ogiltig kursor
        self._persist(session, [{'role': 'assistant', 'content': 'Hej'}])
        self.assertEqual(len(self._lines(session)), 2)

    # ── 5. tool_call_id skiljer ────────────────────────────────────────

    def test_tool_posts_with_different_call_id(self):
        """Två tool-poster med samma text men olika id → två rader."""
        session = self._session()
        self._persist(session, [
            {'role': 'tool', 'content': '{"ok": true}',
             'tool_call_id': 'call_1', 'name': 'odoo_search'}])
        session.write({'pi_message_count': 99})   # ogiltig kursor
        self._persist(session, [
            {'role': 'tool', 'content': '{"ok": true}',
             'tool_call_id': 'call_2', 'name': 'odoo_search'}])
        self.assertEqual(len(self._lines(session)), 2)

    # ── 6. hashen sätts automatiskt ────────────────────────────────────

    def test_content_hash_is_computed(self):
        """content_hash sätts vid create och är md5 av role|id|content."""
        line = self.Line.create({
            'session_id': self._session().id,
            'role': 'user',
            'content': 'hasha mig',
        })
        self.assertTrue(line.content_hash)
        self.assertEqual(len(line.content_hash), 32)
        self.assertEqual(
            line.content_hash,
            self.Line._content_hash(
                {'role': 'user', 'content': 'hasha mig'}))
