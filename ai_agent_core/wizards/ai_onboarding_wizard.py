# -*- coding: utf-8 -*-
"""AI Onboarding Wizard — steg-för-steg intervju och organisationsskapande.

Design: onboarding-posten är source of truth. Wizard-posten skapas en gång
och återanvänds genom hela flödet (persistent record pattern).
"""

import logging
from odoo import models, fields, api, _

_logger = logging.getLogger(__name__)


class AIOnboardingWizard(models.TransientModel):
    _name = 'ai.onboarding.wizard'
    _description = 'AI Onboarding Wizard'

    onboarding_id = fields.Many2one('ai.onboarding', string='Onboarding',
        required=True, readonly=True)

    # Detected info
    detected_modules_text = fields.Text('Installerade moduler', readonly=True)
    website_summary = fields.Text('Webbplats-information', readonly=True)

    # Interview state (read from onboarding record)
    state = fields.Selection([
        ('scan', 'Scan'),
        ('questions', 'Questions'),
        ('proposal', 'Proposal'),
        ('creating', 'Creating'),
        ('done', 'Done'),
    ], default='questions')

    current_question = fields.Text('Fråga', readonly=True)
    current_question_id = fields.Char('Question ID', readonly=True)
    question_options = fields.Text('Alternativ', readonly=True)
    answer = fields.Char('Svar')
    question_index = fields.Integer('Fråga', readonly=True)
    total_questions = fields.Integer('Totalt', readonly=True)

    # Proposal
    proposal_text = fields.Text('Förslag', readonly=True)
    adjustment = fields.Text('Justeringar')

    # Result
    created_summary = fields.Text('Skapad', readonly=True)

    # ════════════════════════════════════════════
    # SKAPA WIZARD FRÅN ONBOARDING
    # ════════════════════════════════════════════

    @api.model
    def _open_wizard(self, onboarding_id, res_id=False):
        """Returnerar en act_window för wizarden."""
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'ai.onboarding.wizard',
            'view_mode': 'form',
            'views': [[False, 'form']],
            'target': 'new',
            'res_id': res_id,
            'context': {'default_onboarding_id': onboarding_id},
        }

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        onboarding_id = self.env.context.get('default_onboarding_id')
        if not onboarding_id:
            return res
        onboarding = self.env['ai.onboarding'].browse(onboarding_id)
        res['onboarding_id'] = onboarding_id

        modules = onboarding.detected_modules or {}
        res['detected_modules_text'] = '\n'.join(
            f'• {m}: {d.get("description", "")}'
            for m, d in modules.items()) or 'Inga kända moduler'
        res['website_summary'] = (onboarding.website_summary or '')[:500]

        if onboarding.state in ('draft', 'scanning'):
            onboarding.action_prepare_questions()

        # Alla frågor besvarade men förslag ej genererat → generera nu
        if onboarding.state in ('analyzing', 'interviewing'):
            questions = onboarding._get_interview_questions()
            answered = onboarding.answers or {}
            remaining = [q for q in questions if q['id'] not in answered]
            if not remaining:
                onboarding.action_generate_proposal()

        if onboarding.state == 'proposal':
            res['state'] = 'proposal'
            self._build_proposal_text(onboarding, res)
        elif onboarding.state == 'completed':
            res['state'] = 'done'
            res['created_summary'] = self._build_created_summary(onboarding)
        else:
            res['state'] = 'questions'
            self._build_question(onboarding, res)
        return res

    def _refresh_from_onboarding(self):
        """Uppdatera denna wizard-post från onboardingens aktuella läge."""
        self.ensure_one()
        onboarding = self.onboarding_id
        modules = onboarding.detected_modules or {}
        self.detected_modules_text = '\n'.join(
            f'• {m}: {d.get("description", "")}'
            for m, d in modules.items()) or 'Inga kända moduler'
        self.website_summary = (onboarding.website_summary or '')[:500]

        if onboarding.state == 'proposal':
            self.state = 'proposal'
            self.proposal_text = self._build_proposal_text(onboarding)
            self.answer = False
        elif onboarding.state == 'completed':
            self.state = 'done'
            self.created_summary = self._build_created_summary(onboarding)
        else:
            self.state = 'questions'
            self.answer = False
            self._build_question(onboarding)

    def _build_question(self, onboarding, res=None):
        """Fyll i nuvarande fråga från onboarding (res = dict eller self)."""
        questions = onboarding._get_interview_questions()
        answered = onboarding.answers or {}
        remaining = [q for q in questions if q['id'] not in answered]
        target = res if isinstance(res, dict) else self
        if remaining:
            q = remaining[0]
            target['current_question'] = q['question']
            target['current_question_id'] = q['id']
            target['question_options'] = '\n'.join(
                f'• {o}' for o in q.get('options', [])) or 'Fritt svar'
            target['question_index'] = len(answered) + 1
            target['total_questions'] = len(questions)
        else:
            target['current_question'] = ''
            target['current_question_id'] = ''
            target['question_options'] = ''
            target['question_index'] = len(answered)
            target['total_questions'] = len(questions)

    def _build_proposal_text(self, onboarding, res=None):
        proposal = onboarding.proposal_json or {}
        lines = []
        if proposal.get('mission'):
            lines.append(f'🎯 Mission:\n{proposal["mission"]}\n')
        if proposal.get('values'):
            lines.append(f'💎 Values:\n{proposal["values"]}\n')
        lines.append('🏢 Avdelningar:')
        for dept in proposal.get('departments', []):
            lines.append(f'\n• {dept.get("name")}:')
            for cw in dept.get('coworkers', []):
                lines.append(f'    🤖 {cw.get("name")} — {cw.get("description", "")}')
        lines.append('\n🥅 Mål:')
        for goal in proposal.get('company_goals', []):
            lines.append(f'• {goal.get("name")}')
        text = '\n'.join(lines)
        if res is not None:
            res['proposal_text'] = text
        return text

    def _build_created_summary(self, onboarding):
        return (
            f'✅ {len(onboarding.created_department_ids)} avdelningar\n'
            f'✅ {len(onboarding.created_coworker_ids)} AI-medarbetare\n'
            f'✅ {len(onboarding.created_goal_ids)} mål\n'
            f'✅ {len(onboarding.created_task_ids)} uppgifter'
        )

    # ════════════════════════════════════════════
    # ACTIONS
    # ════════════════════════════════════════════

    def action_submit_answer(self):
        """Spara svar och uppdatera wizarden till nästa steg (in-place).

        Returnerar ingen action — fältändringarna renderas i dialogfönstret.
        """
        self.ensure_one()
        onboarding = self.onboarding_id

        if self.current_question_id and self.answer:
            onboarding.action_answer(self.current_question_id, self.answer)

        # Är alla frågor besvarade?
        questions = onboarding._get_interview_questions()
        answered = onboarding.answers or {}
        remaining = [q for q in questions if q['id'] not in answered]

        if not remaining:
            # Alla frågor klara → generera förslag
            onboarding.action_generate_proposal()

        # Uppdatera samma wizard-post och visa den igen
        self._refresh_from_onboarding()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'ai.onboarding.wizard',
            'view_mode': 'form',
            'views': [[False, 'form']],
            'target': 'new',
            'context': {'default_onboarding_id': onboarding.id},
        }

    def action_confirm_proposal(self):
        """Godkänn förslaget och skapa organisationen."""
        self.ensure_one()
        onboarding = self.onboarding_id
        onboarding.action_create_organization()
        self._refresh_from_onboarding()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'ai.onboarding.wizard',
            'view_mode': 'form',
            'views': [[False, 'form']],
            'target': 'new',
            'context': {'default_onboarding_id': onboarding.id},
        }

    def action_close(self):
        return {'type': 'ir.actions.act_window_close'}
