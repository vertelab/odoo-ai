# -*- coding: utf-8 -*-
"""Tester för user-mail-ai-interaction (Skiva 2) — promotion, utkast,
mappar, reply-zero, routing, catchall-ingestion."""

import base64
import json
from unittest.mock import patch

from odoo.tests.common import TransactionCase


def make_raw_email(message_id='<inter-test-1@example.com>', subject='Test',
                   frm='Anna <anna@example.com>', to='kalle@vertel.se',
                   body='Hej Kalle!\nHär är ett testmail.',
                   references=None):
    from email.message import EmailMessage
    msg = EmailMessage()
    msg['Message-ID'] = message_id
    msg['Subject'] = subject
    msg['From'] = frm
    msg['To'] = to
    msg['Date'] = 'Mon, 05 Aug 2026 10:00:00 +0200'
    if references:
        msg['References'] = references
    msg.set_content(body)
    return msg.as_bytes()


class TestUserMailAiInteraction(TransactionCase):

    def setUp(self):
        super().setUp()
        self.Mail = self.env['user_mail_ai.mail']
        self.imap = self.env['user.mail.imap']
        self.user = self.env.user
        self.coworker = self.env.ref(
            'user_mail_ai.coworker_mail_assistant', raise_if_not_found=False) \
            or self.env['ai.coworker'].create({
                'name': 'Mail-hjälpredan', 'status': 'active',
                'orchestration_mode': 'single'})
        self.hitl = self.env['ai.coworker.hitl']

    def _ingest(self, raw):
        norm = self.imap._normalize_message(raw, folder='INBOX', uid=42)
        return self.Mail._ingest_message(norm, user=self.user)

    # ── Tråd-matchning & objektkoppling ──────────────────────────────

    def test_thread_match_sets_candidate(self):
        # Skapa ett mail.message på en helpdesk-liknande post (project.task)
        task = self.env['project.task'].create({
            'name': 'Testärende', 'user_ids': [(6, 0, [self.user.id])]})
        self.env['mail.message'].create({
            'model': 'project.task', 'res_id': task.id,
            'message_id': '<ticket-42@example.com>',
            'body': 'Hej', 'message_type': 'email',
        })
        raw = make_raw_email(
            message_id='<reply-1@example.com>',
            references='<ticket-42@example.com>')
        rec = self._ingest(raw)
        cand = json.loads(rec.object_link_candidate or 'null')
        self.assertTrue(cand)
        self.assertEqual(cand['model'], 'project.task')
        self.assertEqual(cand['res_id'], task.id)
        self.assertEqual(cand['source'], 'thread')

    def test_promotion_proposal_and_do(self):
        task = self.env['project.task'].create({
            'name': 'Testärende 2', 'user_ids': [(6, 0, [self.user.id])]})
        self.env['mail.message'].create({
            'model': 'project.task', 'res_id': task.id,
            'message_id': '<ticket-43@example.com>',
            'body': 'Hej', 'message_type': 'email',
        })
        rec = self._ingest(make_raw_email(
            message_id='<reply-2@example.com>',
            references='<ticket-43@example.com>'))
        hitl = rec._propose_promotion()
        self.assertTrue(hitl)
        self.assertEqual(hitl.action_type, 'promote_mail')
        # Godkänn → mail.message skapas på objektet (chatter)
        hitl.with_user(self.user).action_approve()
        posted = self.env['mail.message'].search([
            ('model', '=', 'project.task'),
            ('res_id', '=', task.id),
            ('message_id', '=', '<reply-2@example.com>'),
        ], limit=1)
        self.assertTrue(posted, "Promotion ska skapa mail.message på objektet")
        self.assertEqual(rec.status, 'processed')

    def test_rejected_promotion_stays_private(self):
        rec = self._ingest(make_raw_email(
            message_id='<reply-3@example.com>'))
        rec.write({'object_link_candidate': json.dumps({
            'model': 'project.task', 'res_id': 1,
            'confidence': 0.9, 'source': 'llm'})})
        hitl = rec._propose_promotion()
        hitl.with_user(self.user).action_reject()
        self.assertEqual(rec.status, 'classified')
        self.assertFalse(rec.object_model, "Ingen objektkoppling vid avslag")

    # ── Catchall/mailgateway-ingestion ───────────────────────────────

    def test_ingest_mail_message_with_object(self):
        task = self.env['project.task'].create({
            'name': 'Catchall-ärende', 'user_ids': [(6, 0, [self.user.id])]})
        msg = self.env['mail.message'].create({
            'model': 'project.task', 'res_id': task.id,
            'message_id': '<catch-1@example.com>',
            'email_from': 'kund@example.com',
            'author_id': self.env['res.partner'].create({
                'name': 'Kund', 'email': 'kund@example.com'}).id,
            'subject': 'Supportfråga',
            'body': '<p>Hej, hjälp!</p>',
            'message_type': 'email',
        })
        rec = self.Mail._ingest_mail_message(msg)
        self.assertTrue(rec)
        self.assertEqual(rec.raw_message_id, msg)
        self.assertEqual(rec.object_model, 'project.task')
        self.assertEqual(rec.object_res_id, task.id)
        self.assertEqual(rec.user_id, self.user)

    def test_ingest_skips_internal_author(self):
        # Avsändare = intern användare → ingen triage (loop-skydd)
        msg = self.env['mail.message'].create({
            'model': 'project.task', 'res_id': 1,
            'message_id': '<internal-1@example.com>',
            'author_id': self.user.partner_id.id,
            'body': '<p>Vårt eget svar</p>',
            'message_type': 'email',
        })
        rec = self.Mail._ingest_mail_message(msg)
        self.assertFalse(rec, "Internt mail ska inte ingesteras")

    def test_mail_message_create_hook(self):
        # create-hooken triggar ingestion automatiskt
        task = self.env['project.task'].create({
            'name': 'Hook-ärende', 'user_ids': [(6, 0, [self.user.id])]})
        before = self.Mail.search_count([])
        self.env['mail.message'].create({
            'model': 'project.task', 'res_id': task.id,
            'message_id': '<hook-1@example.com>',
            'email_from': 'kund2@example.com',
            'author_id': self.env['res.partner'].create({
                'name': 'Kund2', 'email': 'kund2@example.com'}).id,
            'subject': 'Fråga',
            'body': '<p>Hej</p>',
            'message_type': 'email',
        })
        after = self.Mail.search_count([])
        self.assertGreater(after, before, "Hook ska ingestera mail.message")

    # ── Reply Zero ───────────────────────────────────────────────────

    def test_reply_zero_defaults_and_update(self):
        rec = self._ingest(make_raw_email())
        self.assertTrue(rec.reply_needed)
        self.assertFalse(rec.awaiting_reply)
        rec.write({'reply_needed': False, 'awaiting_reply': True})
        self.assertTrue(rec.awaiting_reply)

    # ── Specialist-routing ───────────────────────────────────────────

    def test_routing_model_seeded(self):
        routing = self.env['user_mail_ai.routing'].search([
            ('category', '=', 'invoice')], limit=1)
        self.assertTrue(routing, "Faktura-routing ska vara seedad")
        self.assertTrue(routing.coworker_id)

    def test_handoff_uses_active_specialist(self):
        # Skapa routing till en inaktiv specialist → fallback (ingen handoff)
        specialist = self.env['ai.coworker'].create({
            'name': 'Osynlig specialist', 'status': 'inactive',
            'orchestration_mode': 'single'})
        self.env['user_mail_ai.routing'].create({
            'category': 'support', 'coworker_id': specialist.id})
        rec = self._ingest(make_raw_email())
        rec.write({'category': 'support'})
        result = rec._try_handoff()
        self.assertFalse(result, "Inaktiv specialist → ingen handoff")
        self.assertEqual(rec.handoff_state, 'none')

    # ── Mappar & flaggor (utan riktig IMAP — logik-nivå) ─────────────

    def test_move_back_button_present(self):
        # action_move_back finns och kräver AI/Newsletters-folder
        rec = self._ingest(make_raw_email())
        rec.write({'folder': 'AI/Newsletters', 'source_uid': 42})
        # Utan IMAP-anslutning ska den inte krascha hårt — vi mockar imap
        with patch.object(type(self.imap), 'action_move', return_value=True):
            res = rec.action_move_back()
            self.assertTrue(res)
            self.assertEqual(rec.folder, 'INBOX')

    # ── HITL-dispatch (user_mail_ai-överlagring) ─────────────────────

    def test_newsletter_move_rule_sets_user_flag(self):
        hitl = self.coworker._request_hitl(
            'newsletter_move_rule',
            'Flytta nyhetsbrev automatiskt?',
            context={'model': 'user_mail_ai.mail', 'res_id': 1},
            risk_level='safe',
            user_id=self.user.id,
        )
        self.assertFalse(self.user.ai_newsletter_move_enabled)
        hitl.with_user(self.user).action_approve()
        self.assertTrue(self.user.ai_newsletter_move_enabled,
                        "Godkänd standing-rule ska aktivera automatisk flytt")
