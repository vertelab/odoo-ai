# -*- coding: utf-8 -*-
"""Tester för user-mail-ai-intelligence (Skiva 3) — regler, profil, digest,
heartbeat."""

from datetime import datetime, timedelta

from odoo.tests.common import TransactionCase


class TestUserMailAiIntelligence(TransactionCase):

    def setUp(self):
        super().setUp()
        self.Mail = self.env['user_mail_ai.mail']
        self.Rule = self.env['user_mail_ai.rule']
        self.imap = self.env['user.mail.imap']
        self.user = self.env.user

    def _ingest(self, message_id='<intel-1@example.com>',
                subject='Test', frm='Anna <anna@example.com>'):
        from email.message import EmailMessage
        msg = EmailMessage()
        msg['Message-ID'] = message_id
        msg['Subject'] = subject
        msg['From'] = frm
        msg['To'] = 'kalle@vertel.se'
        msg.set_content('Hej!')
        norm = self.imap._normalize_message(msg.as_bytes(), folder='INBOX')
        return self.Mail._ingest_message(norm, user=self.user)

    # ── Regel-modell ─────────────────────────────────────────────────

    def test_rule_create(self):
        rule = self.Rule.create({
            'user_id': self.user.id,
            'name': 'Ignorera nyhetsbrev från X',
            'priority': 5,
            'condition_kind': 'sender',
            'condition_text': 'news@example.com',
            'action': 'ignore',
        })
        self.assertTrue(rule)
        self.assertEqual(rule.source, 'user')

    def test_deterministic_sender_match(self):
        rule = self.Rule.create({
            'user_id': self.user.id,
            'name': 'Ignorera från news',
            'condition_kind': 'sender',
            'condition_text': 'news@example.com',
            'action': 'ignore',
        })
        rec = self._ingest(frm='News <news@example.com>')
        self.assertTrue(rule._matches(rec))
        rec2 = self._ingest(message_id='<intel-2@example.com>',
                            frm='Anna <anna@example.com>')
        self.assertFalse(rule._matches(rec2))

    def test_apply_ignore_rule(self):
        self.Rule.create({
            'user_id': self.user.id,
            'name': 'Ignorera från news',
            'condition_kind': 'sender',
            'condition_text': 'news@example.com',
            'action': 'ignore',
        })
        rec = self._ingest(frm='News <news@example.com>')
        handled, nudged = rec._apply_rules()
        self.assertTrue(handled)
        self.assertFalse(nudged)
        self.assertEqual(rec.status, 'ignored')

    def test_priority_wins(self):
        # Lägre prioritetstal = högre prioritet → move vinner över ignore
        self.Rule.create({
            'user_id': self.user.id, 'name': 'Låg prio ignore',
            'priority': 50, 'condition_kind': 'sender',
            'condition_text': 'news@example.com', 'action': 'ignore',
        })
        self.Rule.create({
            'user_id': self.user.id, 'name': 'Hög prio nudge',
            'priority': 1, 'condition_kind': 'sender',
            'condition_text': 'news@example.com', 'action': 'nudge',
        })
        rec = self._ingest(frm='News <news@example.com>')
        handled, nudged = rec._apply_rules()
        self.assertFalse(handled)
        self.assertTrue(nudged, "Högst prioriterad regel (nudge) ska vinna")

    # ── Default-regler (seed) ────────────────────────────────────────

    def test_ensure_default_rules_idempotent(self):
        self.user.write({'imap_poll_enabled': True})
        first = self.Mail._ensure_default_rules()
        self.assertGreater(first, 0)
        second = self.Mail._ensure_default_rules()
        self.assertEqual(second, 0, "Inga dubletter vid omkörning")
        count = self.Rule.search_count([
            ('user_id', '=', self.user.id),
            ('source', '=', 'seed'),
        ])
        self.assertGreaterEqual(count, 3)

    # ── Klassificeringskontext (LLM-regler + profil) ─────────────────

    def test_prompt_includes_rules_and_profile(self):
        self.Rule.create({
            'user_id': self.user.id, 'name': 'Meddela om missnöje',
            'condition_kind': 'llm',
            'condition_text': 'Kunden är missnöjd',
            'action': 'nudge',
        })
        self.user.write({'ai_profile_text': 'Bryr sig om byggprojekt '
                                            'och kundnöjdhet.'})
        rec = self._ingest()
        prompt = rec._build_classification_prompt()
        self.assertIn('Meddela om missnöje', prompt)
        self.assertIn('byggprojekt', prompt)
        self.assertIn('matched_rules', prompt)

    # ── Intresseprofil ───────────────────────────────────────────────

    def test_finalize_interest_without_embedding(self):
        rec = self._ingest()
        rec.write({'interest_score': 8.0, 'status': 'classified'})
        rec._finalize_interest()
        self.assertEqual(rec.interest_score, 8.0,
                         "Utan embedding behålls LLM-poängen")

    # ── Digest-arkivering ────────────────────────────────────────────

    def test_save_digest_okf(self):
        if 'ai.okf.concept' not in self.env:
            return
        ok = self.Mail._save_digest_okf(
            self.user, 'Sammanfattning av igår', daily=True)
        self.assertTrue(ok)
        concept = self.env['ai.okf.concept'].search([
            ('owner_user_id', '=', self.user.id),
            ('title', 'like', 'Digest daily'),
        ], limit=1)
        self.assertTrue(concept, "Digesten ska arkiveras som OKF-koncept")

    # ── Heartbeat ────────────────────────────────────────────────────

    def test_heartbeat_finds_stale_action_mail(self):
        rec = self._ingest()
        rec.write({
            'action_needed': True,
            'status': 'classified',
            'write_date': (datetime.now() - timedelta(days=5))
            .strftime('%Y-%m-%d %H:%M:%S'),
        })
        items = self.Mail._heartbeat_open_items(self.user, stale_days=2)
        self.assertIn(rec, items)

    def test_heartbeat_finds_unsent_draft(self):
        rec = self._ingest()
        rec.write({
            'draft_uid': 77,
            'status': 'classified',
            'write_date': (datetime.now() - timedelta(days=3))
            .strftime('%Y-%m-%d %H:%M:%S'),
        })
        items = self.Mail._heartbeat_open_items(self.user, stale_days=2)
        self.assertIn(rec, items)

    def test_heartbeat_quiet_for_fresh_mail(self):
        rec = self._ingest()
        rec.write({'action_needed': True, 'status': 'classified'})
        items = self.Mail._heartbeat_open_items(self.user, stale_days=2)
        self.assertNotIn(rec, items, "Färskt mail ska inte nudgas")

    def test_heartbeat_review_updates_follow_up(self):
        rec = self._ingest()
        rec.write({
            'action_needed': True,
            'status': 'classified',
            'write_date': (datetime.now() - timedelta(days=5))
            .strftime('%Y-%m-%d %H:%M:%S'),
        })
        self.Mail._heartbeat_review(user=self.user)
        self.assertTrue(rec.follow_up_at, "Follow-up ska sättas efter nudge")
