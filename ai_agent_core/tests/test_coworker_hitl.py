# -*- coding: utf-8 -*-
"""Tester för ai.coworker.hitl — livscykel, aktivitet, trust-ladder, ACL."""

from odoo.exceptions import AccessError
from odoo.tests.common import TransactionCase
from ._config_param_guard import ConfigParamGuardedCase


class TestCoworkerHITL(TransactionCase):

    def setUp(self):
        super().setUp()
        self.HITL = self.env['ai.coworker.hitl']
        # Minimal coworker (hitl behöver ingen agent för request-flödet)
        self.coworker = self.env['ai.coworker'].create({
            'name': 'HITL-testmedarbetare',
            'orchestration_mode': 'single',
            'status': 'active',
        })
        self.approver = self.env['res.users'].create({
            'name': 'HITL Godkännare',
            'login': 'hitl_approver_%s' % self.env['res.users'].search_count([]),
            'password': 'approver-pass',
            'groups_id': [(6, 0, [self.env.ref('base.group_user').id])],
        })
        self.stranger = self.env['res.users'].create({
            'name': 'HITL Främling',
            'login': 'hitl_stranger_%s' % self.env['res.users'].search_count([]),
            'password': 'stranger-pass',
            'groups_id': [(6, 0, [self.env.ref('base.group_user').id])],
        })

    def _session(self, user=None):
        """En körningssession — HITL kräver en (design D7)."""
        return self.env['ai.coworker.session'].create({
            'coworker_id': self.coworker.id,
            'user_id': (user or self.approver).id,
            'status': 'active',
            'name': 'HITL-testkörning',
        })

    def _request(self, action_type='promote_mail', res_id=1, user=None,
                 session=None):
        sess = session or self._session(user=user)
        return self.coworker.with_context(
            _ai_context_model='ai.coworker.session',
            _ai_context_id=sess.id,
        )._request_hitl(
            action_type,
            'Godkänn promotion av mail till Ticket #%d' % res_id,
            context={'model': 'helpdesk.ticket', 'res_id': res_id},
            risk_level='high',
            user_id=(user or self.approver).id,
        )

    # ── Livscykel ────────────────────────────────────────────────────

    def test_request_creates_asked(self):
        hitl = self._request()
        self.assertTrue(hitl)
        self.assertEqual(hitl.state, 'asked')
        self.assertEqual(hitl.coworker_id, self.coworker)
        self.assertEqual(hitl.user_id, self.approver)
        self.assertEqual(hitl.action_type, 'promote_mail')
        self.assertEqual(hitl.object_type, 'helpdesk.ticket')
        self.assertTrue(hitl.request_summary)

    def test_no_duplicate_open_request(self):
        sess = self._session()
        r1 = self._request(res_id=1, session=sess)
        r2 = self._request(res_id=1, session=sess)
        self.assertEqual(r1.id, r2.id)

    def test_distinct_context_allows_parallel(self):
        r1 = self._request(res_id=1)
        r2 = self._request(res_id=2)
        self.assertNotEqual(r1.id, r2.id)

    # ── Session (ai-coworker-hitl §7) ────────────────────────────────

    def test_request_binds_session_from_context(self):
        """Sessionen löses ur env.context — inte ur ett påhittat fält."""
        sess = self._session()
        hitl = self.coworker.with_context(
            _ai_context_model='ai.coworker.session',
            _ai_context_id=sess.id,
        )._request_hitl('promote_mail', 'Test', context={'model': 'x'})
        self.assertEqual(hitl.session_id, sess)

    def test_request_binds_session_from_lineage_key(self):
        """ai_lineage_session_id är den andra etablerade nyckeln."""
        sess = self._session()
        hitl = self.coworker.with_context(
            ai_lineage_session_id=sess.id,
        )._request_hitl('promote_mail', 'Test', context={'model': 'x'})
        self.assertEqual(hitl.session_id, sess)

    def test_request_without_session_raises(self):
        """Utan session → ValidationError, inte en herrelös request."""
        from odoo.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            self.coworker._request_hitl(
                'promote_mail', 'Test', context={'model': 'x'})

    def test_request_with_dead_session_raises(self):
        """Session-id som pekar på en raderad rad → ValidationError."""
        from odoo.exceptions import ValidationError
        sess = self._session()
        sess_id = sess.id
        sess.unlink()
        with self.assertRaises(ValidationError):
            self.coworker.with_context(
                _ai_context_model='ai.coworker.session',
                _ai_context_id=sess_id,
            )._request_hitl('promote_mail', 'Test', context={'model': 'x'})

    def test_same_context_different_session_is_new_request(self):
        """Dubblettskyddet är per session — två körningar är två frågor."""
        r1 = self._request(res_id=1, session=self._session())
        r2 = self._request(res_id=1, session=self._session())
        self.assertNotEqual(r1.id, r2.id)

    def test_session_smart_button_opens_its_requests(self):
        sess = self._session()
        hitl = self._request(res_id=1, session=sess)
        self.assertEqual(sess.hitl_open_count, 1)
        action = sess.action_open_hitl()
        self.assertEqual(action['res_model'], 'ai.coworker.hitl')
        self.assertIn(('session_id', '=', sess.id), action['domain'])
        self.assertIn(hitl, sess.hitl_ids)

    def test_session_open_count_excludes_decided(self):
        sess = self._session()
        hitl = self._request(res_id=1, session=sess)
        self.assertEqual(sess.hitl_open_count, 1)
        hitl.action_approve()
        self.assertEqual(sess.hitl_open_count, 0)

    def test_approve(self):
        hitl = self._request()
        hitl.with_user(self.approver).action_approve()
        self.assertEqual(hitl.state, 'approved')
        self.assertTrue(hitl.decided_at)
        self.assertEqual(hitl.decided_by, self.approver)

    def test_reject(self):
        hitl = self._request()
        hitl.with_user(self.approver).action_reject()
        self.assertEqual(hitl.state, 'rejected')
        self.assertEqual(hitl.decision, False)  # beslut fylls i av användaren

    def test_expire_stale(self):
        hitl = self._request()
        hitl.write({'create_date': '2020-01-01 00:00:00'})
        expired = self.HITL._expire_stale(days=1)
        self.assertEqual(expired, 1)
        self.assertEqual(hitl.state, 'expired')
        self.assertEqual(hitl.decision, 'timeout')

    # ── Aktiviteter (klockan) ────────────────────────────────────────

    def test_activity_created_and_closed(self):
        hitl = self._request()
        self.assertTrue(hitl.mail_activity_id)
        activity = hitl.mail_activity_id
        self.assertEqual(activity.user_id, self.approver)
        self.assertEqual(activity.res_model, 'ai.coworker.hitl')
        hitl.with_user(self.approver).action_approve()
        # Odoo 18: action_feedback STÄNGER aktiviteten genom att radera den
        # (mail.activity har inget "stängd"-tillstånd kvar). Testet ska
        # därför uttrycka "aktiviteten är inte längre öppen" — inte läsa
        # .active på en rad som inte finns.
        self.assertFalse(activity.exists(),
                         "Aktiviteten ska vara stängd (raderad)")

    # ── Trust-ladder ─────────────────────────────────────────────────

    def _approve_n(self, n, action_type='promote_mail', start=100):
        for i in range(n):
            hitl = self._request(action_type=action_type, res_id=start + i)
            hitl.with_user(self.approver).action_approve()

    def test_auto_proposal_after_n_approvals(self):
        self.env['ir.config_parameter'].set_param(
            'ai_agent_core.hitl_trust_n', '3')
        self._approve_n(3)
        proposal = self.HITL.search([
            ('is_auto_proposal', '=', True),
            ('state', '=', 'asked'),
            ('action_type', '=', 'auto_proposal'),
        ], limit=1)
        self.assertTrue(proposal, "Auto-förslag ska skapas vid N=3")
        self.assertEqual(proposal.object_type, 'helpdesk.ticket')

    def test_no_duplicate_auto_proposal(self):
        self.env['ir.config_parameter'].set_param(
            'ai_agent_core.hitl_trust_n', '3')
        self._approve_n(3)
        # 4:e godkännandet — NYTT ärende (start=200), annars återanvänder
        # _request() den redan godkända requesten och räknaren dubbelräknas.
        self._approve_n(1, start=200)
        count = self.HITL.search_count([
            ('is_auto_proposal', '=', True),
            ('state', '=', 'asked'),
        ])
        self.assertEqual(count, 1)

    def test_approved_proposal_creates_standing_rule(self):
        self.env['ir.config_parameter'].set_param(
            'ai_agent_core.hitl_trust_n', '1')
        self._approve_n(1)
        proposal = self.HITL.search([
            ('is_auto_proposal', '=', True),
            ('state', '=', 'asked'),
        ], limit=1)
        proposal.with_user(self.approver).action_approve()
        self.assertTrue(proposal.standing_rule)
        rule = proposal.standing_rule
        self.assertIn('"action_type": "promote_mail"', rule)
        self.assertIn('helpdesk.ticket', rule)

    def test_rejected_proposal_resets_counter(self):
        self.env['ir.config_parameter'].set_param(
            'ai_agent_core.hitl_trust_n', '3')
        self._approve_n(3)
        proposal = self.HITL.search([
            ('is_auto_proposal', '=', True),
            ('state', '=', 'asked'),
        ], limit=1)
        proposal.with_user(self.approver).action_reject()
        # Nästa godkännande → count=1 (efter avslag) → inget nytt förslag.
        # NYTT ärende (start=200) — annars återanvänds den redan godkända
        # requesten och ingen ny räkning sker.
        self._approve_n(1, start=200)
        proposals = self.HITL.search_count([
            ('is_auto_proposal', '=', True),
            ('state', '=', 'asked'),
        ])
        self.assertEqual(proposals, 0, "Räknaren ska vara nollställd")

    # ── ACL ──────────────────────────────────────────────────────────

    def test_only_approver_can_decide(self):
        hitl = self._request()
        with self.assertRaises(AccessError):
            hitl.with_user(self.stranger).action_approve()

    def test_approver_can_decide(self):
        hitl = self._request()
        hitl.with_user(self.approver).action_approve()
        self.assertEqual(hitl.state, 'approved')

    def test_rule_limits_visibility(self):
        hitl = self._request()
        visible = self.HITL.with_user(self.approver).search(
            [('id', '=', hitl.id)])
        self.assertEqual(len(visible), 1, "Godkännaren ska se requesten")
        hidden = self.HITL.with_user(self.stranger).search(
            [('id', '=', hitl.id)])
        self.assertEqual(len(hidden), 0, "Främlingen ska INTE se requesten")
