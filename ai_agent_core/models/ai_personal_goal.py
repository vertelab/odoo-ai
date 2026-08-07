# -*- coding: utf-8 -*-
"""Personal AI Goals — SMART goals owned by the user."""

import json
import logging
from datetime import date

from odoo import models, fields, api, _
from odoo.exceptions import UserError, AccessError

_logger = logging.getLogger(__name__)


class AIPersonalGoal(models.Model):
    _name = 'ai.personal.goal'
    _description = 'Personal AI Goal'
    _order = 'time_bound, create_date'
    _rec_name = 'name'

    user_id = fields.Many2one(
        'res.users', string='User', required=True,
        default=lambda self: self.env.user,
        help='The user who owns this goal.')
    coworker_id = fields.Many2one(
        'ai.coworker', string='Suggested by Quest',
        help='The AI quest that suggested this goal.')
    okr_id = fields.Reference(
        selection='_selection_okr_targets',
        string='OKR Reference',
        help='Links this personal goal to an OKR objective (okr.objective), '
             'evaluation goal (hr.evaluation.goal), or other goal record.')

    # Core
    name = fields.Char('Goal', required=True)
    description = fields.Text('Description')

    # SMART
    specific = fields.Text('Specific — what exactly?')
    measurable = fields.Text('Measurable — how is it measured?')
    achievable = fields.Text('Achievable — is it realistic?')
    relevant = fields.Text('Relevant — why this goal?')
    time_bound = fields.Date('Time-bound — deadline')

    # Status
    progress = fields.Float('Progress %', default=0.0, group_operator="avg")
    status = fields.Selection([
        ('proposed', 'AI-suggested'),
        ('accepted', 'Accepted'),
        ('active', 'Active'),
        ('completed', 'Completed'),
        ('cancelled', 'Cancelled'),
    ], default='proposed', required=True)

    # Category
    category = fields.Selection([
        ('skill', 'Skill Development'),
        ('knowledge', 'Knowledge Acquisition'),
        ('relationship', 'Relationship Building'),
        ('process', 'Process Improvement'),
        ('career', 'Career Goal'),
        ('productivity', 'Productivity'),
    ], default='skill')

    # Source
    source = fields.Selection([
        ('manual', 'Manual'),
        ('ai_suggested', 'AI-suggested'),
        ('session_extracted', 'Extracted from Session'),
        ('weekly_evolution', 'Weekly Evolution'),
    ], default='manual')

    # Calendar linking
    # Calendar linking (inactive — requires calendar module as dependency)
    # calendar_event_ids = fields.One2many(
    #     'calendar.event', 'ai_goal_id', string='Calendar Blocks')

    # Memory linking
    linked_memory_ids = fields.Many2many(
        'ai.personal.memory', 'ai_goal_memory_rel',
        'goal_id', 'memory_id', string='Related Memories')

    # Metadata
    created_by_ai = fields.Boolean('AI-suggested', default=False)
    last_reviewed = fields.Datetime('Last Reviewed')
    archived = fields.Boolean('Archived', default=False)

    # ── Nudge-engine fields ──
    accept_by_default = fields.Date(
        'Auto-accept by',
        help='If set, the goal auto-activates on this date '
             'unless the user explicitly cancels it. '
             'Default: 3 days after creation for proposed goals.')
    implementation_intention = fields.Char(
        'When & Where',
        help='Implementation intention: when and where the user plans '
             'to work on this goal. E.g. "Friday 09:00, at my desk"')
    streak_count = fields.Integer(
        'Streak (weeks)',
        default=0,
        help='Consecutive weeks with check-in. Reset to 0 on missed check-in.')
    last_checkin = fields.Datetime(
        'Last Check-in',
        help='When the user last confirmed progress on this goal.')
    nudge_count = fields.Integer(
        'Nudge Count',
        default=0,
        help='How many times this goal has been nudged. '
             'Used to avoid over-nudging.')
    source_ref = fields.Reference(
        selection='_selection_source_refs',
        string='Source',
        help='Executive summary or other record that generated this goal.')
    auto_review_interval_days = fields.Integer(
        'Review Interval (days)',
        default=7,
        help='How often the AI should check in on this goal. '
             'Default: weekly.')

    _sql_constraints = [
        ('check_name_not_empty', "CHECK(char_length(name) > 0)", "Goal name required"),
        ('check_progress_range', "CHECK(progress >= 0 AND progress <= 100)", "Progress 0-100"),
    ]

    @api.constrains('status', 'progress')
    def _check_completed(self):
        for r in self:
            if r.status == 'completed' and r.progress < 100:
                raise UserError(_("Completed goals must have 100% progress"))

    @api.model
    def _selection_okr_targets(self):
        """Dynamisk selection för okr_id — endast installerade modeller visas."""
        result = []
        if 'okr.objective' in self.env:
            result.append(('okr.objective', 'OKR Objective'))
        if 'hr.evaluation.goal' in self.env:
            result.append(('hr.evaluation.goal', 'Evaluation Goal'))
        if 'ai.personal.goal' in self.env:
            result.append(('ai.personal.goal', 'Personal Goal'))
        return result

    @api.model
    def _selection_source_refs(self):
        """Dynamisk selection för source_ref — endast installerade modeller."""
        result = []
        if 'ai.company.memory' in self.env:
            result.append(('ai.company.memory', 'Company Memory'))
        if 'strategy.review' in self.env:
            result.append(('strategy.review', 'Strategy Review'))
        if 'ai.coworker.session' in self.env:
            result.append(('ai.coworker.session', 'AI Session'))
        return result

    def action_accept(self):
        """Accept an AI-suggested goal."""
        self.ensure_one()
        if self.status != 'proposed':
            raise UserError(_("Only proposed goals can be accepted"))
        self.write({'status': 'active', 'created_by_ai': True})

    def action_complete(self):
        """Mark goal as completed."""
        self.ensure_one()
        self.write({'status': 'completed', 'progress': 100.0})

    def action_cancel(self):
        """Cancel a goal."""
        self.ensure_one()
        self.write({'status': 'cancelled', 'archived': True})

    def action_book_calendar(self, recurrence='weekly'):
        """Create recurring calendar events for this goal.
        Requires calendar module. Currently disabled.
        """
        raise UserError(_("Calendar integration requires calendar module. Coming soon."))

    @api.model
    def search_for_user(self, user_id, status=None):
        """Find goals for a user, optionally filtered by status."""
        domain = [('user_id', '=', user_id), ('archived', '=', False)]
        if status:
            domain.append(('status', '=', status))
        return self.search(domain, order='deadline, create_date')

    @api.model
    def suggest_goals_from_evolution(self, user_id, signals):
        """Generate goal suggestions from weekly signals."""
        existing = self.search_for_user(user_id, status='active')
        existing_names = [g.name.lower() for g in existing]
        suggestions = []
        for signal in signals:
            name = signal.get('name', '').strip()
            if name.lower() not in existing_names and len(name) > 3:
                suggestions.append(self.create({
                    'user_id': user_id,
                    'name': name,
                    'specific': signal.get('specific', ''),
                    'measurable': signal.get('measurable', ''),
                    'achievable': signal.get('achievable', ''),
                    'relevant': signal.get('relevant', ''),
                    'time_bound': signal.get('time_bound'),
                    'category': signal.get('category', 'skill'),
                    'source': 'weekly_evolution',
                    'created_by_ai': True,
                    'status': 'proposed',
                }))
        return suggestions
