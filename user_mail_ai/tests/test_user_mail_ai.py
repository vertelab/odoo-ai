# -*- coding: utf-8 -*-
"""Tester för user_mail_ai — triage, ingestion, partner, Teams→calendar."""

import base64
from datetime import datetime, timedelta

from odoo.tests.common import TransactionCase


def make_raw_email(message_id='<ai-test-1@example.com>', subject='Test',
                   frm='Anna <anna@example.com>', to='kalle@vertel.se',
                   body='Hej Kalle!\nHär är ett testmail.', with_ics=False):
    from email.message import EmailMessage
    msg = EmailMessage()
    msg['Message-ID'] = message_id
    msg['Subject'] = subject
    msg['From'] = frm
    msg['To'] = to
    msg['Date'] = 'Mon, 05 Aug 2026 10:00:00 +0200'
    msg.set_content(body)
    if with_ics:
        ics = (
            'BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Test//Test//EN\r\n'
            'BEGIN:VEVENT\r\nUID:test-uid-1@example.com\r\n'
            'SUMMARY:Teams-möte med Anna\r\n'
            'DTSTART:20260810T140000Z\r\nDTEND:20260810T150000Z\r\n'
            'ORGANIZER:mailto:anna@example.com\r\n'
            'DESCRIPTION:https://teams.microsoft.com/l/meetup-join/abc\r\n'
            'END:VEVENT\r\nEND:VCALENDAR\r\n'
        )
        msg.add_attachment(ics, filename='invite.ics',
                           maintype='text', subtype='calendar')
    return msg.as_bytes()


class TestUserMailAi(TransactionCase):

    def setUp(self):
        super().setUp()
        self.Mail = self.env['user_mail_ai.mail']
        self.imap = self.env['user.mail.imap']
        self.user = self.env.user
        self.imap_model = self.env['user.mail.imap']

    def _ingest_raw(self, raw, user=None):
        norm = self.imap_model._normalize_message(raw, folder='INBOX')
        return self.Mail._ingest_message(norm, user=user or self.user)

    # ── Modell & livscykel ──────────────────────────────────────────

    def test_model_exists(self):
        self.assertTrue(self.Mail)
        self.assertIn('reply_suggested', self.Mail._fields)
        self.assertIn('object_link_candidate', self.Mail._fields)

    def test_ingest_creates_triage(self):
        rec = self._ingest_raw(make_raw_email())
        self.assertTrue(rec)
        self.assertEqual(rec.status, 'new')
        self.assertEqual(rec.subject, 'Test')
        self.assertEqual(rec.user_id, self.user)
        self.assertEqual(rec.message_id, '<ai-test-1@example.com>')
        self.assertEqual(rec.folder, 'INBOX')

    def test_ingest_dedup(self):
        raw = make_raw_email()
        r1 = self._ingest_raw(raw)
        r2 = self._ingest_raw(raw)
        self.assertEqual(r1.id, r2.id)

    def test_lifecycle(self):
        rec = self._ingest_raw(make_raw_email())
        rec.action_mark_processed()
        self.assertEqual(rec.status, 'processed')
        rec.action_reclassify()
        self.assertEqual(rec.status, 'new')

    # ── Partner-resolution ──────────────────────────────────────────

    def test_partner_resolved_or_created(self):
        rec = self._ingest_raw(make_raw_email(
            frm='Anna <anna@example.com>'))
        self.assertTrue(rec.partner_id)
        self.assertEqual(rec.partner_id.email, 'anna@example.com')

    def test_partner_reused_for_known_email(self):
        raw = make_raw_email()
        rec = self._ingest_raw(raw)
        partner = rec.partner_id
        rec2 = self._ingest_raw(make_raw_email(
            message_id='<ai-test-2@example.com>'))
        self.assertEqual(rec2.partner_id.id, partner.id)

    # ── OKF-arkiv + eml ─────────────────────────────────────────────

    def test_raw_eml_attachment_stored(self):
        rec = self._ingest_raw(make_raw_email())
        att = self.env['ir.attachment'].search([
            ('res_model', '=', 'user_mail_ai.mail'),
            ('res_id', '=', rec.id)], limit=1)
        self.assertTrue(att)
        self.assertEqual(att.mimetype, 'message/rfc822')
        self.assertIn(b'<ai-test-1@example.com>',
                      base64.b64decode(att.datas))

    def test_okf_archive_created(self):
        if 'ai.okf.concept' not in self.env:
            return
        rec = self._ingest_raw(make_raw_email())
        concepts = self.env['ai.okf.concept'].search([
            ('source_ref', '=', rec.message_id)], limit=1)
        self.assertTrue(concepts, "OKF-koncept ska finnas för mailet")

    # ── Teams-detektering ───────────────────────────────────────────

    def test_detect_teams_via_ics_attachment(self):
        rec = self._ingest_raw(make_raw_email(with_ics=True))
        self.assertTrue(rec._detect_teams_invite())

    def test_detect_teams_via_subject(self):
        rec = self._ingest_raw(make_raw_email(
            subject='Inbjudan till Teams-möte'))
        self.assertTrue(rec._detect_teams_invite())

    def test_no_teams_for_normal_mail(self):
        rec = self._ingest_raw(make_raw_email())
        self.assertFalse(rec._detect_teams_invite())

    # ── Teams-inbjudan → calendar.event ─────────────────────────────

    def test_teams_handler_creates_event(self):
        rec = self._ingest_raw(make_raw_email(with_ics=True))
        rec._handle_teams_invite()
        self.assertTrue(rec.calendar_event_id)
        event = rec.calendar_event_id
        self.assertEqual(event.name, 'Teams-möte med Anna')
        self.assertIn('teams.microsoft.com', event.description or '')
        self.assertEqual(event.start,
                         datetime(2026, 8, 10, 14, 0, 0))
        self.assertEqual(event.stop, datetime(2026, 8, 10, 15, 0, 0))
        self.assertEqual(rec.status, 'processed')

    def test_teams_handler_no_duplicate(self):
        rec = self._ingest_raw(make_raw_email(with_ics=True))
        event1 = rec._handle_teams_invite()
        event2 = rec._handle_teams_invite()
        self.assertEqual(event1.id, event2.id)
        count = self.env['calendar.event'].search_count([
            ('name', '=', 'Teams-möte med Anna')])
        self.assertEqual(count, 1)

    # ── Klassificering (deterministisk del, ingen LLM i test) ───────

    def test_classify_teams_without_provider(self):
        rec = self._ingest_raw(make_raw_email(with_ics=True))
        rec._classify()
        self.assertTrue(rec.teams_invite)
        self.assertEqual(rec.status, 'classified')

    def test_classify_without_provider_graceful(self):
        rec = self._ingest_raw(make_raw_email())
        rec._classify()
        # Utan default-modell ska det inte krascha — antingen klassat eller
        # kvar i new med notis.
        self.assertIn(rec.status, ('new', 'classified'))

    # ── Hjälpredan (seed) ───────────────────────────────────────────

    def test_coworker_seeded(self):
        coworker = self.env.ref(
            'user_mail_ai.coworker_mail_assistant', raise_if_not_found=False)
        self.assertTrue(coworker)
        self.assertEqual(coworker.name, 'Mail-hjälpredan')
        self.assertTrue(coworker.agent_ids)
        self.assertTrue(coworker.heartbeat_enabled)

    def test_graph_node_definition(self):
        definition = self.env['graph.node.definition'].search([
            ('graph_label', '=', 'MailMessage')], limit=1)
        self.assertTrue(definition)
        self.assertEqual(definition.model_id.model, 'user_mail_ai.mail')

    # ── Integritet (ACL/record-rule) ────────────────────────────────

    def test_privacy_rule_exists(self):
        rule = self.env['ir.rule'].search([
            ('model_id.model', '=', 'user_mail_ai.mail')], limit=1)
        self.assertTrue(rule)
        self.assertIn('user_id', rule.domain_force or '')
