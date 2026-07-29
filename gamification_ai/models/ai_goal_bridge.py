# -*- coding: utf-8 -*-
"""Connect gamification badges/challenges with AI personal goals."""

from odoo import models, fields, api, _
from odoo.exceptions import UserError
import logging

_logger = logging.getLogger(__name__)


class AIPersonalGoal(models.Model):
    _inherit = 'ai.personal.goal'

    challenge_line_ids = fields.One2many(
        'gamification.challenge.line', 'ai_goal_id',
        string='Challenge Lines',
        help='Gamification challenges this goal is part of.')

    def action_complete(self):
        """Mark goal as completed and trigger badge assignment."""
        result = super().action_complete()
        for goal in self:
            # Award badge
            if goal.category:
                self._award_badge(goal, f'ai_goal_{goal.category}')
            self._check_challenge_progress(goal)
        return result

    @api.model
    def _award_badge(self, goal, badge_code):
        """Award a badge when a goal is completed."""
        Badge = self.env['gamification.badge']
        badge = Badge.search([('predefined_category', '=', badge_code)], limit=1)
        if not badge:
            return
        if not self.env['gamification.badge.user'].search([
            ('badge_id', '=', badge.id),
            ('user_id', '=', goal.user_id.id),
        ], limit=1):
            self.env['gamification.badge.user'].sudo().create({
                'badge_id': badge.id,
                'user_id': goal.user_id.id,
                'challenge_id': goal._get_active_challenge(goal.user_id).id,
            })

    @api.model
    def _check_challenge_progress(self, goal):
        """Update challenge progress when a goal completes."""
        Challenge = self.env['gamification.challenge']
        challenges = Challenge.search([
            ('user_id', '=', goal.user_id.id),
            ('state', '=', 'in_progress'),
        ])
        for challenge in challenges:
            lines = challenge.line_ids.filtered(lambda l: l.ai_goal_id == goal)
            for line in lines:
                line.target_progress = 100.0

    @api.model
    def _get_active_challenge(self, user):
        """Get or create an active challenge for the user."""
        Challenge = self.env['gamification.challenge']
        challenge = Challenge.search([
            ('user_id', '=', user.id),
            ('state', 'in', ['draft', 'in_progress']),
        ], limit=1)
        if challenge:
            return challenge
        return Challenge.create({
            'name': f'{user.name}: Personal Development',
            'user_id': user.id,
            'start_date': fields.Date.today(),
            'end_date': fields.Date.today() + timedelta(days=90),
        })
