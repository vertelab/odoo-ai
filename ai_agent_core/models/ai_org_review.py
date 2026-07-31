# -*- coding: utf-8 -*-
"""AI Organization Reviews — periodisk prestationsutvärdering."""

from odoo import models, fields, api, _


class AIOrgReview(models.Model):
    _name = 'ai.org.review'
    _description = 'AI Performance Review'
    _rec_name = 'display_name'
    _order = 'period_start desc'

    agent_id = fields.Many2one('ai.agent', string='Agent',
        required=True, index=True)
    reviewer_id = fields.Many2one('ai.agent', string='Reviewer (AI)',
        help='AI-agenten som gör utvärderingen.')
    human_reviewer_id = fields.Many2one('res.users',
        string='Reviewer (Human)',
        help='Människan som gör/godkänner utvärderingen.')

    period = fields.Selection([
        ('weekly', 'Weekly'),
        ('monthly', 'Monthly'),
        ('quarterly', 'Quarterly'),
        ('annual', 'Annual'),
    ], required=True, default='monthly')

    period_start = fields.Date(required=True)
    period_end = fields.Date(required=True)

    # Innehåll
    accomplishments = fields.Text('Accomplishments')
    challenges = fields.Text('Challenges')
    areas_for_improvement = fields.Text('Areas for Improvement')

    # Metrics
    score = fields.Selection([
        ('1', '1 — Underperforming'),
        ('2', '2 — Needs Improvement'),
        ('3', '3 — Meets Expectations'),
        ('4', '4 — Exceeds Expectations'),
        ('5', '5 — Outstanding'),
    ], string='Score')

    # Goal progress during period
    goal_ids = fields.Many2many('ai.org.goal',
        'ai_org_review_goal_rel',
        'review_id', 'goal_id',
        string='Goals This Period')

    # Rekommendation
    recommendation = fields.Selection([
        ('promote', 'Promote'),
        ('expand', 'Expand Role'),
        ('train', 'Additional Training'),
        ('maintain', 'Maintain'),
        ('monitor', 'Monitor'),
        ('restructure', 'Restructure Role'),
        ('terminate', 'Terminate'),
    ], string='Recommendation')

    # Status
    state = fields.Selection([
        ('draft', 'Draft'),
        ('reviewed', 'Reviewed'),
        ('acknowledged', 'Acknowledged'),
    ], default='draft')

    display_name = fields.Char(compute='_compute_display_name')

    @api.depends('agent_id.name', 'period', 'period_start')
    def _compute_display_name(self):
        for r in self:
            agent = r.agent_id.name or '?'
            period = dict(r._fields['period'].selection).get(r.period, '?')
            r.display_name = f'Review: {agent} — {period} {r.period_start}'
