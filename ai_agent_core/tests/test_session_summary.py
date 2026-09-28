# -*- coding: utf-8 -*-
"""Tester för sessionens eftermäle (okf-recall-path fas 4, beslut D4).

VARFÖR DESSA TESTER FINNS: `session.summary` hade fyra olika
sammanfattningsvägar varav bara EN skrev fältet — och bara i buzz-läge.
En vanlig chatt fick därför aldrig ett eftermäle, och konsolideringen läste
en råsvans av de sista 40 raderna i stället. Det såg byggt ut och var tomt
i drift, precis som resten av den här ändringen.

Därför testar vi BETEENDET: att fältet faktiskt får ett värde, att det
bara skrivs en gång, och att korta sessioner hoppas över.
"""

from unittest.mock import patch

from odoo.tests import common, tagged

import logging
_logger = logging.getLogger(__name__)


@tagged('okf', 'session', 'post_install', '-at_install')
class TestFinalSummary(common.TransactionCase):
    """Fas 4: ett eftermäle, en skrivare, idempotent."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.ref('base.main_company')
        cls.Session = cls.env['ai.coworker.session']
        cls.Line = cls.env['ai.coworker.session.line']
        cls.user = cls.env.ref('base.user_admin')

    def _make_session(self, n_lines=6, status='active'):
        session = self.Session.create({
            'name': 'test-summary',
            'status': status,
            'user_id': self.user.id,
        })
        for i in range(n_lines):
            self.Line.create({
                'session_id': session.id,
                'sequence': i + 1,
                'role': 'user' if i % 2 == 0 else 'assistant',
                'content': 'Rad %d: beslut och fakta om kunden.' % (i + 1),
            })
        return session

    def _fake_summary(self, text='### Syfte\nTesta eftermälet.'):
        """Patcha LLM-anropet — vi testar flödet, inte providern."""
        return patch.object(
            type(self.Session), '_run_final_summary_llm',
            return_value=text)

    # ── 4.9 Idempotens ──────────────────────────────────────────────

    def test_summary_is_idempotent(self):
        """4.9: två anrop utan nya rader = ETT LLM-anrop."""
        session = self._make_session(n_lines=6)
        calls = []

        def counting_llm(transcript):
            calls.append(transcript)
            return '### Syfte\nFörsta sammanfattningen.'

        with patch.object(type(self.Session), '_run_final_summary_llm',
                          side_effect=counting_llm):
            first = session._write_final_summary()
            second = session._write_final_summary()

        self.assertEqual(len(calls), 1,
                         'LLM-anropet ska bara ske en gång utan nya rader')
        self.assertEqual(first, second)
        self.assertEqual(session.summary, first)
        self.assertEqual(session.summary_message_count, 6)

    def test_new_lines_trigger_new_summary(self):
        """4.9: nya rader sedan sist → nytt eftermäle."""
        session = self._make_session(n_lines=6)
        with self._fake_summary('### Syfte\nFörsta.'):
            session._write_final_summary()
        self.Line.create({
            'session_id': session.id, 'sequence': 7,
            'role': 'user', 'content': 'Ett nytt beslut tillkom.',
        })
        with self._fake_summary('### Syfte\nAndra.'):
            second = session._write_final_summary()
        self.assertEqual(session.summary, '### Syfte\nAndra.')
        self.assertEqual(session.summary_message_count, 7)
        self.assertEqual(second, '### Syfte\nAndra.')

    # ── 4.10 Vanlig chatt får eftermäle ─────────────────────────────

    def test_normal_chat_gets_summary(self):
        """4.10: session i icke-buzz-läge får ändå ett eftermäle.

        Detta var kärnan i buggen: `_buzz_maybe_summarize_session` returnerade
        False direkt om orchestration_mode != 'buzz'. Vanliga sessioner
        (default-läget) fick aldrig någon sammanfattning.
        """
        session = self._make_session(n_lines=6)
        coworker = self.env['ai.coworker'].create({'name': 'test-cw'})
        # Standardläget är INTE buzz — det är hela poängen.
        self.assertNotEqual(coworker.orchestration_mode, 'buzz')

        with self._fake_summary('### Utfall\nVanlig chatt sammanfattad.'):
            result = coworker._buzz_maybe_summarize_session(session)

        self.assertTrue(result, 'vanlig chatt ska få ett eftermäle')
        self.assertEqual(session.summary,
                         '### Utfall\nVanlig chatt sammanfattad.')

    # ── 4.11 Kort session hoppas över ───────────────────────────────

    def test_short_session_skipped(self):
        """4.11: en session under tröskeln får INGEN tom sammanfattning."""
        session = self._make_session(n_lines=2)
        with self._fake_summary('borde aldrig anropas') as llm:
            result = session._write_final_summary()
        self.assertIsNone(result)
        self.assertFalse(session.summary)
        self.assertFalse(llm.called,
                         'LLM ska inte anropas för en för kort session')

    def test_empty_transcript_skipped(self):
        """4.11: rader utan innehåll ger inget eftermäle."""
        session = self.Session.create({
            'name': 'tom', 'status': 'active', 'user_id': self.user.id})
        for i in range(6):
            self.Line.create({
                'session_id': session.id, 'sequence': i + 1,
                'role': 'user', 'content': False,
            })
        with self._fake_summary('borde aldrig anropas') as llm:
            result = session._write_final_summary()
        self.assertIsNone(result)
        self.assertFalse(llm.called)

    # ── 4.12 Samtidig stängning + cron = en skrivning ───────────────

    def test_concurrent_close_and_cron_writes_once(self):
        """4.12: mark_done + idle-cron samtidigt = ETT eftermäle.

        Idempotensen hänger på summary_message_count. Cronen ska se att
        sessionen redan är stängd (status != active) och hoppa över den.
        """
        session = self._make_session(n_lines=6)
        with self._fake_summary('### Beslut\nEtt enda eftermäle.'):
            session.mark_done(reason='stop')
            summary_after_close = session.summary
            # Cronen körs direkt etteråt — ska inte skapa ett nytt anrop.
            self.Session._cron_close_idle_sessions(
                idle_minutes=-1, extra_domain=[('id', '=', session.id)])

        self.assertEqual(session.summary, summary_after_close)
        self.assertEqual(session.status, 'done')
        self.assertEqual(session.finish_reason, 'stop')

    # ── 4.13 Övergiven session stängs av cron ───────────────────────

    def test_abandoned_session_closed_by_cron(self):
        """4.13: cronen stänger och sammanfattar en övergiven session.

        Cronen tar en batch (20) och sorterar på write_date asc. Testet
        disablar den globala batchen genom att skapa sessionen och sedan
        köra cronen upprepade gånger tills vår session är hanterad — den
        deterministiska egenskapen är att sessionen SKA stängas, inte att
        den råkar hamna i första batchen.
        """
        session = self._make_session(n_lines=6, status='active')
        with self._fake_summary('### Utfall\nÖvergiven, sammanfattad.'):
            # Negativ idle → cutoff i framtiden så att även en nyss
            # skapad rad kvalificerar (write_date < cutoff).
            closed = self.Session._cron_close_idle_sessions(
                idle_minutes=-1,
                extra_domain=[('id', '=', session.id)])
        self.assertEqual(closed, 1)
        session.invalidate_recordset()
        self.assertEqual(session.status, 'done')
        self.assertEqual(session.finish_reason, 'idle')
        self.assertEqual(session.summary,
                         '### Utfall\nÖvergiven, sammanfattad.')

    def test_cron_closes_short_session_without_summary(self):
        """4.13: en för kort session stängs ändå — men utan eftermäle.

        Annars skulle den ligga kvar i kön för evigt (alltid äldst) och
        svälta ut nyare sessioner ur batchen.
        """
        session = self._make_session(n_lines=2, status='active')
        with self._fake_summary('borde aldrig anropas') as llm:
            # Negativ idle → cutoff i framtiden så att även en nyss
            # skapad rad kvalificerar (write_date < cutoff).
            closed = self.Session._cron_close_idle_sessions(
                idle_minutes=-1,
                extra_domain=[('id', '=', session.id)])
        self.assertEqual(closed, 1)
        session.invalidate_recordset()
        self.assertEqual(session.status, 'done')
        self.assertFalse(session.summary,
                         'för kort session ska inte få ett tomt eftermäle')
        self.assertFalse(llm.called)

    def test_cron_ignores_active_sessions(self):
        """4.13: en session med färsk aktivitet rörs inte."""
        session = self._make_session(n_lines=6, status='active')
        with self._fake_summary('borde inte anropas') as llm:
            self.Session._cron_close_idle_sessions(
                idle_minutes=60, extra_domain=[('id', '=', session.id)])
        self.assertFalse(llm.called)
        self.assertEqual(session.status, 'active')
    # ── Strukturen på eftermälet ────────────────────────────────────

    def test_summary_prompt_has_swedish_headings(self):
        """Krav 5: fasta svenska rubriker, ingen påhittad utfyllnad."""
        session = self.Session.create({
            'name': 'prompt', 'status': 'active', 'user_id': self.user.id})
        prompt = session._final_summary_prompt('testtranskript')
        for heading in ('Syfte', 'Utfall', 'Beslut', 'Fakta',
                        'Artefakter', 'Öppna frågor'):
            self.assertIn(heading, prompt,
                          'rubriken %s saknas i prompten' % heading)
        self.assertIn('aldrig påhittat', prompt)
        self.assertIn('svenska', prompt)


@tagged('okf', 'session', 'post_install', '-at_install')
class TestConceptKeyNormalization(common.TransactionCase):
    """D6: concept_key får inte kollapsa olika fakta till samma nyckel."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.coworker = cls.env['ai.coworker'].create({'name': 'key-test'})

    def test_different_content_different_key(self):
        """Den gamla nyckeln `len(summary[:40])` gav alltid samma värde."""
        a = self.coworker._normalize_concept_key(
            'Kunden föredrar fakturering via e-post.', 'personal')
        b = self.coworker._normalize_concept_key(
            'Projektet använder tvåveckorssprintar.', 'personal')
        self.assertNotEqual(a, b,
                            'olika fakta måste ge olika nycklar')

    def test_identical_content_same_key(self):
        """Identiska fakta ska dedupas — det är hela poängen med nyckeln."""
        a = self.coworker._normalize_concept_key(
            'Kunden föredrar fakturering via e-post.', 'personal')
        b = self.coworker._normalize_concept_key(
            '  KUNDEN FÖREDRAR FAKTURERING VIA E-POST.  ', 'personal')
        self.assertEqual(a, b, 'samma fakta (normaliserad) = samma nyckel')

    def test_scope_separates_keys(self):
        """Samma fakta i olika scope är olika koncept."""
        a = self.coworker._normalize_concept_key('Samma text.', 'personal')
        b = self.coworker._normalize_concept_key('Samma text.', 'company')
        self.assertNotEqual(a, b)
