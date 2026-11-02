# -*- coding: utf-8 -*-
"""Karaktäriseringstester för mail-kanalen (channel-adapter-wiring §1).

Ändring: channel-adapter-wiring (2026-11-02), tasks 1.1–1.2 och 4.2–4.4.

VARFÖR: `ai.coworker.mail()` migreras till det gemensamma
`process_item()`-kontraktet. En migrering är bara en refaktor om beteendet
är detsamma före och efter — och det kan bara bevisas med tester som
skrivs INNAN migreringen och körs OFÖRÄNDRADE efter.

Testerna fångar därför det som faktiskt händer i dag:

  1. sessionen skapas med `init_type='mail'` och namnet `Mail: <ämne>`
  2. bilagorna extraheras in i prompten under `## Attachments`
  3. `run()` anropas med brödtexten som prompt
  4. svaret skickas via `_send_mail_reply()` (inte via `message_post`)
  5. tom brödtext → `None`, ingen session, inget `run()`
  6. ett fel i `run()` → `status='error'`, `finish_reason='error'`,
     felet i `error_detail`, och `None` returneras

Inga LLM-anrop görs: `run()` patchas. Bilageextraktionen (`_extract_text`)
körs på riktigt — den är en del av beteendet som ska bevaras.
"""

import base64
from unittest.mock import patch

from odoo.tests.common import TransactionCase

from odoo.addons.ai_agent_core.models import ai_coworker as coworker_module


class MailChannelCharacterization(TransactionCase):
    """Beteendet hos `ai.coworker.mail()` — mätt före migreringen."""

    def setUp(self):
        super().setUp()
        self.coworker = self.env['ai.coworker'].create({
            'name': 'Mail-kanal karaktärisering',
            'status': 'active',
        })
        self.sender = 'kund@example.com'
        self.message = self.env['mail.message'].create({
            'model': 'res.partner',
            'res_id': self.env.user.partner_id.id,
            'message_type': 'email',
            'subject': 'Fakturafråga',
            'body': '<p>Hej, kan du förklara fakturan?</p>',
            'email_from': self.sender,
            'message_id': '<karaktarisering-1@example.com>',
        })

    # ── hjälpare ────────────────────────────────────────────────────

    def _run_mail(self, result='Svar från agenten', side_effect=None):
        """Kör mail() med patchad run() + patchad svarsutskick.

        Returnerar (returvärde, run-anrop, skickade svar).
        """
        run_calls = []
        sent = []

        def _fake_run(prompt=None, session=None, **kwargs):
            run_calls.append({'prompt': prompt, 'session': session})
            if side_effect is not None:
                raise side_effect
            # Efterlikna run(): assistant-raden är det som skickas tillbaka.
            self.env['ai.coworker.session.line'].create({
                'session_id': session.id,
                'sequence': 2,
                'role': 'assistant',
                'content': result,
            })
            return result

        def _fake_send(mail_message, reply_text, quest):
            sent.append({
                'message': mail_message,
                'reply_text': reply_text,
                'quest': quest,
            })

        with patch.object(type(self.coworker), 'run', _fake_run), \
                patch.object(coworker_module, '_send_mail_reply', _fake_send):
            value = self.coworker.mail(self.message)
        return value, run_calls, sent

    # ── 1.1 — session, prompt, run(), svar ──────────────────────────

    def test_session_is_created_with_init_type_mail(self):
        """Sessionen skapas med init_type='mail' och namnet efter ämnet."""
        before = self.env['ai.coworker.session'].search_count(
            [('coworker_id', '=', self.coworker.id)])
        self._run_mail()
        sessions = self.env['ai.coworker.session'].search(
            [('coworker_id', '=', self.coworker.id)])
        self.assertEqual(len(sessions), before + 1,
                         'exakt en ny session ska skapas')
        session = sessions.sorted('id')[-1]
        self.assertEqual(session.init_type, 'mail')
        self.assertEqual(session.name, 'Mail: Fakturafråga')

    def test_prompt_is_the_plain_text_body(self):
        """Brödtexten blir prompten (HTML → text)."""
        _value, run_calls, _sent = self._run_mail()
        self.assertEqual(len(run_calls), 1, 'run() ska anropas en gång')
        self.assertEqual(run_calls[0]['prompt'],
                         'Hej, kan du förklara fakturan?')
        self.assertTrue(run_calls[0]['session'],
                        'run() ska få sessionen, inte skapa en egen')

    def test_reply_is_sent_via_send_mail_reply(self):
        """Svaret skickas via _send_mail_reply() — inte via message_post."""
        value, _run_calls, sent = self._run_mail(result='Här är svaret.')
        self.assertEqual(value, 'Här är svaret.')
        self.assertEqual(len(sent), 1, 'exakt ett svar ska skickas')
        self.assertEqual(sent[0]['reply_text'], 'Här är svaret.')
        self.assertEqual(sent[0]['message'], self.message)
        self.assertEqual(sent[0]['quest'], self.coworker)

    def test_reply_is_truncated_to_4000_chars(self):
        """Svaret kapas till 4000 tecken innan det skickas."""
        long_answer = 'x' * 5000
        _value, _run_calls, sent = self._run_mail(result=long_answer)
        self.assertEqual(len(sent[0]['reply_text']), 4000)

    # ── 1.2 — bilagor, tom brödtext, fel ────────────────────────────

    def test_attachment_text_is_appended_to_the_prompt(self):
        """Bilageinnehållet hamnar under '## Attachments' i prompten."""
        self.env['ir.attachment'].create({
            'name': 'anteckning.txt',
            'datas': base64.b64encode(b'Bilageinnehall har'),
            'res_model': 'mail.message',
            'res_id': self.message.id,
        })
        self.message.invalidate_recordset(['attachment_ids'])
        _value, run_calls, _sent = self._run_mail()
        prompt = run_calls[0]['prompt']
        self.assertIn('## Attachments', prompt)
        self.assertIn('--- anteckning.txt ---', prompt)
        self.assertIn('Bilageinnehall har', prompt)
        self.assertTrue(prompt.startswith('Hej, kan du förklara fakturan?'),
                        'brödtexten ska stå först')

    def test_without_attachments_the_prompt_has_no_attachment_section(self):
        """Utan bilagor finns ingen '## Attachments'-rubrik."""
        _value, run_calls, _sent = self._run_mail()
        self.assertNotIn('## Attachments', run_calls[0]['prompt'])

    def test_empty_body_returns_none_without_running(self):
        """Tom brödtext → None, ingen session, inget run()."""
        self.message.body = '<p>   </p>'
        before = self.env['ai.coworker.session'].search_count(
            [('coworker_id', '=', self.coworker.id)])
        value, run_calls, sent = self._run_mail()
        self.assertIsNone(value)
        self.assertEqual(run_calls, [], 'run() ska inte anropas')
        self.assertEqual(sent, [], 'inget svar ska skickas')
        self.assertEqual(
            self.env['ai.coworker.session'].search_count(
                [('coworker_id', '=', self.coworker.id)]),
            before, 'ingen session ska skapas')

    def test_failure_sets_status_and_finish_reason_error(self):
        """Ett fel i run() → status='error', finish_reason='error'."""
        value, run_calls, sent = self._run_mail(
            side_effect=RuntimeError('provider nere'))
        self.assertIsNone(value)
        self.assertEqual(len(run_calls), 1)
        self.assertEqual(sent, [], 'inget svar ska skickas vid fel')
        session = self.env['ai.coworker.session'].search(
            [('coworker_id', '=', self.coworker.id)],
            order='id desc', limit=1)
        self.assertEqual(session.status, 'error')
        self.assertEqual(session.finish_reason, 'error')
        self.assertIn('provider nere', session.error_detail)

    def test_quest_error_skips_the_channel(self):
        """En coworker med felaktig konfiguration rör inte mailet."""
        broken = self.env['ai.coworker'].create({'name': 'Trasig', 'status': 'active'})
        with patch.object(type(broken), '_check_quest_error',
                          return_value='agent saknas'):
            value = broken.mail(self.message)
        self.assertIsNone(value)
        self.assertFalse(
            self.env['ai.coworker.session'].search(
                [('coworker_id', '=', broken.id)]),
            'ingen session ska skapas när konfigurationen är trasig')
