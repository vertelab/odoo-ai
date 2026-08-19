# -*- coding: utf-8 -*-
"""ai.coworker.hitl — record-baserad HITL (human-in-the-loop) för coworkers.

- Livscykel: asked → approved | rejected | expired.
- Aktivitet: mail.activity (todo) i klockan; beslut → done.
- Trust-ladder: N liknande godkännanden → auto-förslag → standing rule.
- API för coworkers: coworker._request_hitl(action_type, summary, context, ...).

Konsumeras av domänbryggor (mail-hjälpredan, social-kanal) — ingen
domänlogik här.
"""

import hashlib
import json
import logging
from datetime import timedelta

from odoo import api, fields, models
from odoo.exceptions import AccessError

_logger = logging.getLogger(__name__)


class AICoworkerHITL(models.Model):
    _name = 'ai.coworker.hitl'
    _description = 'AI Medarbetare: HITL-request'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'create_date desc, id desc'

    coworker_id = fields.Many2one(
        'ai.coworker', string='AI Medarbetare', required=True,
        ondelete='cascade', index=True)
    user_id = fields.Many2one(
        'res.users', string='Godkännare', required=True,
        ondelete='cascade', index=True)
    state = fields.Selection([
        ('asked', 'Frågad'),
        ('approved', 'Godkänd'),
        ('rejected', 'Avslagen'),
        ('expired', 'Utgången'),
    ], string='Status', default='asked', index=True)
    risk_level = fields.Selection([
        ('safe', 'Safe'),
        ('high', 'Hög risk'),
        ('destructive', 'Destruktiv'),
    ], string='Risknivå', default='high')
    action_type = fields.Char(
        string='Åtgärdstyp', required=True, index=True,
        help='T.ex. promote_mail, send_reply, social_publish.')
    object_type = fields.Char(
        string='Objekttyp', index=True,
        help='Härleds ur context.model — används av trust-ladder.')
    context_hash = fields.Char(
        string='Kontext-hash', index=True,
        help='md5(action_type + context) — dubblettskydd för öppna requests.')
    request_summary = fields.Text(string='Förfrågan', required=True)
    context = fields.Text(
        string='Kontext (JSON)', readonly=True,
        help='Payload: model/res_id/förslag — endast godkännare/admin ser.')
    mail_activity_id = fields.Many2one(
        'mail.activity', string='Aktivitet', readonly=True)
    decision = fields.Text(string='Beslut')
    decided_at = fields.Datetime(string='Beslutat')
    decided_by = fields.Many2one('res.users', string='Beslutat av')
    is_auto_proposal = fields.Boolean(
        string='Auto-förslag', default=False, index=True,
        help='Trust-ladder: fråga om automatisering.')
    standing_rule = fields.Text(
        string='Standing rule (JSON)', readonly=True)

    # ── Rättigheter ──────────────────────────────────────────────────

    def _check_decide_rights(self):
        """Endast godkännaren (user_id) eller admin får besluta."""
        self.ensure_one()
        user = self.env.user
        if not (user.has_group('base.group_system')
                or user.id == self.user_id.id):
            raise AccessError(
                'Endast godkännaren eller admin kan besluta om denna '
                'HITL-request.')

    # ── Livscykel ────────────────────────────────────────────────────

    def action_approve(self):
        for rec in self:
            rec._check_decide_rights()
            rec.write({
                'state': 'approved',
                'decided_at': fields.Datetime.now(),
                'decided_by': self.env.user.id,
            })
            rec._close_activity()
            if rec.is_auto_proposal:
                rec._create_standing_rule()
            else:
                rec._maybe_propose_automation()
        return True

    def action_reject(self):
        for rec in self:
            rec._check_decide_rights()
            rec.write({
                'state': 'rejected',
                'decided_at': fields.Datetime.now(),
                'decided_by': self.env.user.id,
            })
            rec._close_activity()
            # Avslag på auto-förslag → räknaren nollställs implicit via
            # _approval_count (senaste avslagna auto-förslaget är gräns).
        return True

    @api.model
    def _expire_stale(self, days=None):
        """Cron: utgångna öppna requests → state=expired, decision=timeout."""
        if days is None:
            param = self.env['ir.config_parameter'].sudo().get_param(
                'ai_agent_core.hitl_expire_days', '7')
            try:
                days = int(param)
            except Exception:
                days = 7
        cutoff = fields.Datetime.now() - timedelta(days=days)
        stale = self.search([
            ('state', '=', 'asked'),
            ('create_date', '<', cutoff),
        ])
        for rec in stale:
            rec.write({
                'state': 'expired',
                'decision': 'timeout',
                'decided_at': fields.Datetime.now(),
            })
            rec._close_activity()
        return len(stale)

    # ── Aktiviteter (klockan) ────────────────────────────────────────

    def _create_activity(self):
        """Skapa mail.activity (todo) för godkännaren — syns i klockan."""
        self.ensure_one()
        todo_type = self.env.ref(
            'mail.mail_activity_data_todo', raise_if_not_found=False)
        try:
            activity = self.activity_schedule(
                'mail.mail_activity_data_todo',
                summary='HITL: %s — %s' % (
                    self.action_type, self.coworker_id.name),
                note=(self.request_summary or '')[:1000],
                user_id=self.user_id.id,
            )
            self.write({'mail_activity_id': activity.id})
            return activity
        except Exception as e:
            _logger.warning('HITL activity_schedule failed: %s', e)
            return False

    def _close_activity(self):
        """Stäng aktiviteten vid beslut (done)."""
        self.ensure_one()
        if self.mail_activity_id:
            try:
                self.mail_activity_id.action_feedback(
                    feedback=('Beslut: %s' % self.state))
            except Exception as e:
                _logger.warning('HITL activity close failed: %s', e)

    def _notify(self):
        """Odoo-notis (bell) till godkännaren vid skapande."""
        self.ensure_one()
        try:
            self.message_post(
                body=('HITL-begäran: <b>%s</b> — %s'
                      % (self.action_type, self.request_summary)),
                message_type='notification',
                partner_ids=[self.user_id.partner_id.id])
        except Exception as e:
            _logger.warning('HITL notify failed: %s', e)
        self._push_notify()

    def _push_notify(self):
        """Web push till godkännaren (via web_pwa_push). Tyst utan enheter/nycklar."""
        self.ensure_one()
        try:
            self.env['web.pwa.push']._push_user_notification(
                self.user_id,
                title='HITL: %s' % (self.coworker_id.name or 'AI-medarbetare'),
                body=self.request_summary or 'Väntar på ditt godkännande',
                url='/odoo/ai.coworker.hitl/%s' % self.id)
        except Exception as e:
            _logger.warning('HITL push failed: %s', e)

    # ── Trust-ladder ─────────────────────────────────────────────────

    def _approval_count(self, user_id, action_type, object_type):
        """Antal godkända (icke auto-proposal) — efter senaste avslaget
        auto-förslag (avslag nollställer räknaren)."""
        last_reject = self.search([
            ('user_id', '=', user_id),
            ('action_type', '=', action_type),
            ('object_type', '=', object_type or ''),
            ('is_auto_proposal', '=', True),
            ('state', '=', 'rejected'),
        ], order='create_date desc', limit=1)
        domain = [
            ('user_id', '=', user_id),
            ('action_type', '=', action_type),
            ('object_type', '=', object_type or ''),
            ('state', '=', 'approved'),
            ('is_auto_proposal', '=', False),
        ]
        if last_reject:
            domain.append(('create_date', '>', last_reject.create_date))
        return self.search_count(domain)

    def _maybe_propose_automation(self):
        """Vid N godkända liknande → föreslå automatisering (ingen dubbel)."""
        self.ensure_one()
        if self.is_auto_proposal:
            return False
        param = self.env['ir.config_parameter'].sudo().get_param(
            'ai_agent_core.hitl_trust_n', '3')
        try:
            n = int(param)
        except Exception:
            n = 3
        count = self._approval_count(
            self.user_id.id, self.action_type, self.object_type)
        if count < n:
            return False
        existing = self.search([
            ('user_id', '=', self.user_id.id),
            ('is_auto_proposal', '=', True),
            ('state', '=', 'asked'),
            ('action_type', '=', self.action_type),
            ('object_type', '=', self.object_type or ''),
        ], limit=1)
        if existing:
            return existing
        proposal = self.sudo().create({
            'coworker_id': self.coworker_id.id,
            'user_id': self.user_id.id,
            'action_type': 'auto_proposal',
            'object_type': self.object_type or '',
            'request_summary': (
                'Vill du att jag gör "%s" för %s automatiskt nästa gång? '
                'Du har godkänt %d liknande.' % (
                    self.action_type,
                    self.object_type or 'denna typ',
                    count)),
            'context': json.dumps({
                'action_type': self.action_type,
                'object_type': self.object_type or '',
            }),
            'risk_level': 'safe',
            'is_auto_proposal': True,
        })
        proposal._create_activity()
        proposal._notify()
        return proposal

    def _create_standing_rule(self):
        """Godkänt auto-förslag → standing-rule-rad + hook för konsumenter."""
        self.ensure_one()
        try:
            ctx = json.loads(self.context or '{}')
        except Exception:
            ctx = {}
        rule = {
            'user_id': self.user_id.id,
            'action_type': ctx.get('action_type') or self.action_type,
            'object_type': ctx.get('object_type') or self.object_type or '',
            'created_at': fields.Datetime.now().isoformat(),
            'source_hitl_id': self.id,
        }
        self.write({'standing_rule': json.dumps(rule)})
        self._on_standing_rule_created(rule)
        return rule

    def _on_standing_rule_created(self, rule):
        """Hook — domänkonsumenter (mail-regelmodell, social-kanal)
        ärver/registrerar sig för att agera på standing rules."""
        return True


class AICoworkerHITLMixin(models.Model):
    """ai.coworker — HITL-API + smartknapp."""
    _inherit = 'ai.coworker'

    hitl_open_count = fields.Integer(
        string='Öppna HITL', compute='_compute_hitl_open_count')

    @api.depends()
    def _compute_hitl_open_count(self):
        HITL = self.env['ai.coworker.hitl']
        for rec in self:
            rec.hitl_open_count = HITL.search_count([
                ('coworker_id', '=', rec.id),
                ('state', '=', 'asked'),
            ])

    def _request_hitl(self, action_type, summary, context=None,
                      risk_level='high', user_id=None):
        """Begär mänskligt godkännande — record + aktivitet + notis.

        Dubblettskydd: samma (coworker, action_type, context-hash) med
        öppen 'asked' → returnerar befintlig request.

        Args:
            action_type: t.ex. 'promote_mail', 'send_reply', 'social_publish'.
            summary: läsbar beskrivning för godkännaren.
            context: dict (model, res_id, förslag…) — lagras som JSON.
            risk_level: safe | high | destructive.
            user_id: godkännaren (res.users). Default: env.user.
        """
        self.ensure_one()
        user = self.env['res.users'].browse(user_id) if user_id \
            else self.env.user
        if not user:
            user = self.env.ref('base.user_root')
        ctx = context or {}
        ctx_json = json.dumps(ctx, sort_keys=True)
        ctx_hash = hashlib.md5(
            ('%s|%s' % (action_type, ctx_json)).encode('utf-8')
        ).hexdigest()[:16]
        object_type = ctx.get('model') or ''

        existing = self.env['ai.coworker.hitl'].search([
            ('coworker_id', '=', self.id),
            ('state', '=', 'asked'),
            ('action_type', '=', action_type),
            ('context_hash', '=', ctx_hash),
            ('user_id', '=', user.id),
        ], limit=1)
        if existing:
            return existing

        hitl = self.env['ai.coworker.hitl'].sudo().create({
            'coworker_id': self.id,
            'user_id': user.id,
            'action_type': action_type,
            'object_type': object_type,
            'context_hash': ctx_hash,
            'request_summary': summary,
            'context': ctx_json,
            'risk_level': risk_level,
            'state': 'asked',
        })
        hitl._create_activity()
        hitl._notify()
        return hitl

    def action_open_hitl(self):
        """Smartknapp: öppna HITL-requests för denna coworker."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'HITL-requests — %s' % self.name,
            'res_model': 'ai.coworker.hitl',
            'view_mode': 'list,form',
            'views': [[False, 'list'], [False, 'form']],
            'domain': [('coworker_id', '=', self.id)],
            'context': {'search_default_asked': 1},
        }


class ResUsersHITL(models.Model):
    """res.users — smartknapp i Min profil: mina HITL-godkännanden."""
    _inherit = 'res.users'

    hitl_approver_count = fields.Integer(
        string='HITL-godkännanden', compute='_compute_hitl_approver_count')

    @api.depends()
    def _compute_hitl_approver_count(self):
        HITL = self.env['ai.coworker.hitl']
        for rec in self:
            rec.hitl_approver_count = HITL.search_count([
                ('user_id', '=', rec.id),
                ('state', '=', 'asked'),
            ])

    def action_open_my_hitl(self):
        """Smartknapp (Min profil): öppna HITL där jag är godkännare."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Mina HITL-godkännanden',
            'res_model': 'ai.coworker.hitl',
            'view_mode': 'list,form',
            'views': [[False, 'list'], [False, 'form']],
            'domain': [('user_id', '=', self.id)],
            'context': {'search_default_asked': 1},
        }
