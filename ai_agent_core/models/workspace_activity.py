# -*- coding: utf-8 -*-
"""Workspace Activity Suggestion — Agenda + förslag + godkännandekön.

Tasks 5.1-5.7, 5.5b-5.5d:
- 5.1 Agenda-vy (query-byggare): mål, möten, PARA-projekt, coworker-initiativ, godkännandekö
- 5.2 GAP-analys (target−current från KR, deadline−idag från SMART) → förslag
- 5.3 HITL "förbereda + fråga": Acceptera/Redigera/Avvisa → godkänt blir riktigt objekt
- 5.4 Tvånivå-OKR-filtrering (D14)
- 5.5 Godkännandekön (Approvals-vyn)
- 5.5b Snabbåtgärder i agendan (gap D5): Godkänn / Avvisa / Omplanera i kortet
- 5.5c "Varför?"-vy (gap B1): källorna bakom slutsatsen
- 5.5d Diff-visning (gap C1): före/efter för de fält som skrivs
- 5.7 "Mötets ankare" (D13): knytning mail/koncept/not → calendar.event via object_ref
"""

import json
import logging
from datetime import date, timedelta

from odoo import models, fields, api, _

_logger = logging.getLogger(__name__)


class WorkspaceActivitySuggestion(models.Model):
    """Ett aktivitetsförslag i agendan — HITL-kort, inte ett riktigt objekt."""

    _name = 'workspace.activity.suggestion'
    _description = 'Workspace Activity Suggestion'
    _order = 'priority desc, create_date asc'
    _rec_name = 'summary'

    priority = fields.Integer('Prioritet', default=10)

    # ── Ägare + ursprung ──
    user_id = fields.Many2one('res.users', string='User', required=True,
        default=lambda self: self.env.user)
    coworker_id = fields.Many2one('ai.coworker', string='Suggested by')
    session_id = fields.Many2one(
        'ai.coworker.session', string='Session',
        help='Coworker-session som producerade förslaget (lineage).')
    source = fields.Selection([
        ('gap_okr', 'OKR GAP'),
        ('smart_deadline', 'SMART Deadline'),
        ('pará_project', 'PARA Project'),
        ('meeting_context', 'Meeting Context'),
        ('coworker', 'Coworker Initiativ'),
        ('manual', 'Manual'),
    ], string='Source', default='coworker')

    # ── Innehåll ──
    summary = fields.Char('Förslag', required=True)
    detail = fields.Text('Detalj')
    suggestion_type = fields.Selection([
        ('calendar.event', 'Boka möte'),
        ('mail.activity', 'Skapa todo/aktivitet'),
        ('dms.file', 'Skapa dokument'),
        ('sale.order', 'Skapa offert'),
        ('account.move', 'Skapa faktura'),
        ('other', 'Annat'),
    ], string='Typ', default='mail.activity')

    # ── Målkoppling (GAP) ──
    personal_goal_id = fields.Many2one('ai.personal.goal', string='Personal Goal')
    org_goal_id = fields.Many2one('ai.org.goal', string='Org Goal')
    key_result_id = fields.Many2one('ai.org.key_result', string='Key Result')

    # ── Status (HITL-flöde) ──
    state = fields.Selection([
        ('proposed', 'Proposed'),
        ('accepted', 'Accepted'),
        ('rejected', 'Rejected'),
        ('done', 'Done'),
    ], string='State', default='proposed')

    # ── 5.5d Diff-visning (C1): före/efter för de fält som skrivs ──
    diff_before = fields.Json('Before', help='{"stage_id": 1, "deadline": "2026-07-01"}')
    diff_after = fields.Json('After', help='{"stage_id": 3, "deadline": "2026-07-15"}')

    # ── 5.5c "Varför?"-vy (B1): källorna bakom slutsatsen ──
    evidence_ids = fields.Many2many(
        'ai.okf.concept', 'workspace_sugg_evidence_rel',
        'suggestion_id', 'concept_id', string='Källkoncept',
        help='Klickbara referenser som motiverar förslaget (Varför?-vyn).')

    # ── 5.7 Mötets ankare (D13) ──
    meeting_anchor = fields.Reference(
        selection=lambda self: self._selection_anchor_models(),
        string='Mötets ankare',
        help='object_ref till calendar.event som detta material knyts till.')

    # ── Resultat ──
    result_ref = fields.Char('Resultat', help='res_model,id när godkänt objekt skapats')

    # ── 6.2 Länk tillbaka: ärende → källkoncept ──
    source_concept_id = fields.Many2one(
        'ai.okf.concept', string='Källkoncept',
        help='Konceptet detta initiativ arbetar med — express-objektet '
             'länkas tillbaka hit.')

    # ── Livscykel ──
    rejected_at = fields.Datetime('Rejected At')
    accepted_at = fields.Datetime('Accepted At')
    active = fields.Boolean('Active', default=True)

    def _selection_anchor_models(self):
        models = [('calendar.event', 'Calendar Event'),
                  ('sale.order', 'Sale Order'),
                  ('project.task', 'Project Task'),
                  ('crm.lead', 'CRM Lead'),
                  ('account.move', 'Invoice')]
        for name in ('calendar.event', 'project.task', 'crm.lead'):
            if name not in self.env:
                models = [m for m in models if m[0] != name]
        return models

    @api.model
    def _create_suggestion(self, summary, suggestion_type='mail.activity',
                           source='coworker', user=None, coworker_id=None,
                           session_id=None,
                           personal_goal_id=None, org_goal_id=None,
                           key_result_id=None, detail=None, diff_before=None,
                           diff_after=None, evidence_ids=None,
                           meeting_anchor=None):
        """Create a proposed suggestion (task 5.3: HITL-kort).

        Loggar lineage-edge session_to_suggestion (session → förslag).
        """
        user = user or self.env.user
        suggestion = self.create({
            'user_id': user.id,
            'coworker_id': coworker_id,
            'session_id': session_id,
            'summary': summary,
            'detail': detail,
            'suggestion_type': suggestion_type,
            'source': source,
            'personal_goal_id': personal_goal_id,
            'org_goal_id': org_goal_id,
            'key_result_id': key_result_id,
            'diff_before': diff_before,
            'diff_after': diff_after,
            'evidence_ids': [(6, 0, evidence_ids or [])],
            'meeting_anchor': meeting_anchor,
        })
        # Lineage: session → förslag
        if session_id and 'ai.lineage.link' in self.env:
            self.env['ai.lineage.link']._add_edge(
                'session_to_suggestion',
                f'ai.coworker.session,{session_id}',
                f'workspace.activity.suggestion,{suggestion.id}')
        # Lineage: concept_evidence (källa → förslag: konceptet stödjer förslaget)
        if evidence_ids and 'ai.lineage.link' in self.env:
            for cid in evidence_ids:
                self.env['ai.lineage.link']._add_edge(
                    'concept_evidence',
                    f'ai.okf.concept,{cid}',
                    f'workspace.activity.suggestion,{suggestion.id}')
        return suggestion

    def write(self, vals):
        """Spegla nya evidence_ids som concept_evidence-edges (ADD-only)."""
        res = super().write(vals)
        if vals.get('evidence_ids') and 'ai.lineage.link' in self.env:
            for rec in self:
                for cmd in vals['evidence_ids']:
                    # (4, id, 0) = link; (6, 0, ids) = replace-all
                    if cmd and cmd[0] == 4:
                        self.env['ai.lineage.link']._add_edge(
                            'concept_evidence',
                            f'ai.okf.concept,{cmd[1]}',
                            f'workspace.activity.suggestion,{rec.id}')
                    elif cmd and cmd[0] == 6:
                        for cid in cmd[2]:
                            self.env['ai.lineage.link']._add_edge(
                                'concept_evidence',
                                f'ai.okf.concept,{cid}',
                                f'workspace.activity.suggestion,{rec.id}')
        return res

    # ── 5.5b Snabbåtgärder i agendan ──

    def action_accept(self):
        """Acceptera förslaget (task 5.3 + 5.5b): skapar riktigt objekt.

        Loggar lineage-edge suggestion_to_action (förslag → Odoo-objekt).
        """
        for rec in self:
            result = rec._materialize()
            rec.write({
                'state': 'accepted' if result else 'rejected',
                'accepted_at': fields.Datetime.now(),
                'active': False,
                'result_ref': result,
            })
            # Lineage: förslag → skapat Odoo-objekt (result = 'res_model,id')
            if result and 'ai.lineage.link' in self.env:
                self.env['ai.lineage.link']._add_edge(
                    'suggestion_to_action',
                    f'workspace.activity.suggestion,{rec.id}',
                    result,
                    note=f'Godkänt av {rec.user_id.name}')
        return True

    def action_reject(self):
        """Avvisa förslaget — lärande: avvisanden på nivå 2 göms helt (D14)."""
        self.write({
            'state': 'rejected',
            'rejected_at': fields.Datetime.now(),
            'active': False,
        })
        return True

    def action_reschedule(self):
        """Omplanera — sätt deadline om 3 dagar (kort i agendan)."""
        self.write({
            'state': 'proposed',
        })
        return {
            'type': 'ir.actions.act_window',
            'name': 'Omplanera',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }

    def action_why(self):
        """5.5c 'Varför?'-vy (B1) — visar HELA lineage-kedjan.

        Kedja: åtgärd ← förslag ← session ← injicerade koncept ← källor
        (via evidence/concept_evidence + session_to_suggestion +
        suggestion_to_action + concept_injected).
        """
        self.ensure_one()
        Lineage = self.env.get('ai.lineage.link')
        edge_ids = []
        if Lineage:
            # Alla edges som rör förslaget (bakåt: session→förslag,
            # concept_evidence; framåt: förslag→åtgärd)
            edges = Lineage.search([
                ('from_model', '=', 'workspace.activity.suggestion'),
                ('from_id', '=', self.id),
            ])
            edges |= Lineage.search([
                ('to_model', '=', 'workspace.activity.suggestion'),
                ('to_id', '=', self.id),
            ])
            # Plus koncept_injected för förslagets session (kontexten)
            if self.session_id:
                edges |= Lineage.search([
                    ('kind', '=', 'concept_injected'),
                    ('from_model', '=', 'ai.coworker.session'),
                    ('from_id', '=', self.session_id.id),
                ])
            edge_ids = edges.ids

        if not edge_ids:
            # Fallback till gamla beteendet: visa källkoncepten
            if not self.evidence_ids:
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {'title': 'Ingen lineage',
                               'message': 'Detta förslag har inga kopplade källkoncept eller edges.',
                               'type': 'info', 'sticky': False},
                }
            return {
                'type': 'ir.actions.act_window',
                'name': 'Varför? — källkoncept',
                'res_model': 'ai.okf.concept',
                'view_mode': 'list,form',
                'domain': [('id', 'in', self.evidence_ids.ids)],
            }
        return {
            'type': 'ir.actions.act_window',
            'name': 'Varför? — lineage-kedja',
            'res_model': 'ai.lineage.link',
            'view_mode': 'list,form',
            'domain': [('id', 'in', edge_ids)],
            'help': 'Kedjan: ÅTGÄRD ← FÖRSLAG ← SESSION ← KONCEPT ← KÄLLA',
        }

    # ── Materialisering: förslag → riktigt Odoo-objekt ──

    def _materialize(self):
        """Skapa riktigt objekt baserat på suggestion_type (OpenWorker-gate:
        sker bara efter HITL-godkännande, dvs i action_accept)."""
        self.ensure_one()
        env = self.env

        # Nudge → mail.activity på målobjektet (D5)
        if self.suggestion_type == 'mail.activity':
            target = self._target_record()
            if not target:
                # Inget mål: skapa en todo på användaren via calendar/activity
                return self._create_default_activity()
            return self._create_activity_on(target)

        if self.suggestion_type == 'calendar.event':
            if 'calendar.event' not in env:
                return False
            meeting = env['calendar.event'].create({
                'name': self.summary,
                'description': self.detail or '',
                'start': fields.Datetime.now(),
                'stop': fields.Datetime.now() + timedelta(hours=1),
                'partner_ids': [(6, 0, [self.user_id.partner_id.id])],
            })
            return 'calendar.event,%d' % meeting.id

        if self.suggestion_type == 'dms.file':
            if 'dms.file' not in env:
                return False
            dms = env['dms.file'].create({
                'name': self.summary,
            })
            return 'dms.file,%d' % dms.id

        return False

    def _target_record(self):
        """Målobjekt för aktiviteten: från mötesankare, KR, mål eller default."""
        if self.meeting_anchor:
            model, _, rid = str(self.meeting_anchor).rpartition(',')
            if model in self.env:
                rec = self.env[model].browse(int(rid))
                if rec.exists():
                    return rec
        if self.key_result_id:
            return self.key_result_id
        if self.personal_goal_id:
            return self.personal_goal_id
        if self.org_goal_id:
            return self.org_goal_id
        return False

    def _create_activity_on(self, target):
        if not hasattr(target, 'activity_schedule'):
            return False
        try:
            # 6.1: nudges → mail.activity med deadline (om 3 dagar), sorterbar
            deadline = date.today() + timedelta(days=3)
            act = target.activity_schedule(
                'mail.mail_activity_data_todo',
                summary=self.summary,
                note=self.detail or '',
                date_deadline=deadline.isoformat(),
                user_id=self.user_id.id or self.env.uid,
            )
            if act:
                # 6.2: objektet länkas tillbaka till källkonceptet
                if self.source_concept_id:
                    try:
                        self.source_concept_id.message_post(
                            body='Express-åtgärd: %s (%s)' % (
                                self.summary,
                                'mail.activity,%d' % act.id))
                    except Exception:
                        _logger.debug('Länkning till koncept misslyckades')
                return 'mail.activity,%d' % act.id
        except Exception as e:
            _logger.warning('activity_schedule failed: %s', e)
        return False

    def _create_default_activity(self):
        # Fallback: todo via mail.activity på användarens partner
        try:
            act = self.user_id.partner_id.activity_schedule(
                'mail.mail_activity_data_todo',
                summary=self.summary,
                note=self.detail or '',
                user_id=self.user_id.id or self.env.uid,
            )
            return 'mail.activity,%d' % act.id if act else False
        except Exception as e:
            _logger.warning('default activity failed: %s', e)
            return False


# ════════════════════════════════════════════
# GAP-analysmotorn (task 5.2 + 5.4)
# ════════════════════════════════════════════

class WorkspaceGapEngine(models.AbstractModel):
    """GAP-analys: target−current från KR, deadline−idag från SMART."""

    _name = 'workspace.gap.engine'
    _description = 'Workspace GAP Engine'

    @api.model
    def suggest_for_user(self, user=None, max_suggestions=10):
        """Generera aktivitetsförslag för en användare (task 5.2).

        Nivå 1 (direkt): roll/coworker-kopplade OKR + KR-assignee → full GAP.
        Nivå 2 (indirekt): kaskad via avdelning → komprimerad kontext (5.4).
        """
        user = user or self.env.user
        Suggestion = self.env['workspace.activity.suggestion']
        suggestions = []

        # ── Nivå 1: personliga mål (SMART) — alltid full GAP ──
        if 'ai.personal.goal' in self.env:
            today = date.today()
            goals = self.env['ai.personal.goal'].search([
                ('user_id', '=', user.id),
                ('status', 'in', ('accepted', 'active')),
                ('archived', '=', False),
            ])
            for goal in goals:
                if goal.time_bound:
                    delta = (goal.time_bound - today).days
                    if 0 <= delta <= 7:
                        suggestions.append(
                            Suggestion._create_suggestion(
                                summary='Deadline om %d dagar: %s'
                                        % (delta, goal.name),
                                detail='SMART-målet har deadline %s och '
                                       'progress %.0f%%.'
                                       % (goal.time_bound, goal.progress),
                                suggestion_type='calendar.event',
                                source='smart_deadline',
                                user=user,
                                personal_goal_id=goal.id,
                                diff_before={'progress': goal.progress},
                                diff_after={'progress': 100.0},
                            ))

        # ── Nivå 1: OKR som ägs direkt (KR-assignee) ──
        if 'ai.org.key_result' in self.env:
            krs = self.env['ai.org.key_result'].search([])
            for kr in krs:
                # Direkt koppling saknas i modellen; använd goal.user_id som
                # proxy för "tilldelad till användaren" (D14 nivå 1).
                if kr.goal_id.user_id and kr.goal_id.user_id.id == user.id:
                    gap = kr.target_value - kr.current_value
                    if gap > 0 and kr.progress < 100:
                        suggestions.append(
                            Suggestion._create_suggestion(
                                summary='KR-gap: %s (%.0f → %.0f %s)'
                                        % (kr.name, kr.current_value,
                                           kr.target_value, kr.unit),
                                detail='GAP = %.0f %s. Nuvarande progress '
                                       '%.1f%%.' % (gap, kr.unit, kr.progress),
                                suggestion_type='mail.activity',
                                source='gap_okr',
                                user=user,
                                org_goal_id=kr.goal_id.id,
                                key_result_id=kr.id,
                                diff_before={'current_value': kr.current_value},
                                diff_after={'current_value': kr.target_value},
                            ))
                if len(suggestions) >= max_suggestions:
                    break

        return suggestions

    @api.model
    def build_agenda(self, user=None):
        """5.1 Agenda-vyn (query-byggare): samlar allt vid öppning.

        Returnerar dict med domäner per sektion så agendan kan byggas
        som flera actions.
        """
        user = user or self.env.user
        today = date.today()

        agenda = {
            'personal_goals': [],
            'meetings': [],
            'para_projects': [],
            'suggestions': [],
            'approvals': [],
        }

        # Mål
        if 'ai.personal.goal' in self.env:
            agenda['personal_goals'] = self.env['ai.personal.goal'].search([
                ('user_id', '=', user.id),
                ('status', 'in', ('accepted', 'active')),
                ('archived', '=', False),
            ]).ids

        # Dagens möten med grafkontext (calendar.event)
        if 'calendar.event' in self.env:
            meetings = self.env['calendar.event'].search([
                ('partner_ids', 'in', [user.partner_id.id]),
                ('start', '>=', fields.Datetime.to_string(
                    fields.Datetime.now().replace(hour=0, minute=0))),
                ('start', '<=', fields.Datetime.to_string(
                    fields.Datetime.now().replace(hour=23, minute=59))),
            ])
            agenda['meetings'] = meetings.ids

        # Aktiva PARA-projekt
        if 'workspace.para.container' in self.env:
            agenda['para_projects'] = self.env['workspace.para.container'].search([
                ('user_id', '=', user.id),
                ('kind', '=', 'project'),
                ('state', '=', 'active'),
            ]).ids

        # Förslag + godkännanden
        if 'workspace.activity.suggestion' in self.env:
            agenda['suggestions'] = self.env['workspace.activity.suggestion'].search([
                ('user_id', '=', user.id),
                ('state', '=', 'proposed'),
                ('active', '=', True),
            ]).ids

        return agenda
