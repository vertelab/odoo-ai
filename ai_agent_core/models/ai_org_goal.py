# -*- coding: utf-8 -*-
"""AI Organization Goals — hierarkiskt OKR-system med cascade."""

import logging
from odoo import models, fields, api, _

_logger = logging.getLogger(__name__)


class AIOrgGoal(models.Model):
    _name = 'ai.org.goal'
    _description = 'AI Organization Goal'
    _rec_name = 'name'
    _order = 'level, sequence, id'

    name = fields.Char(required=True)
    description = fields.Text()

    level = fields.Selection([
        ('company', 'Company'),
        ('department', 'Department'),
        ('coworker', 'Coworker'),
        ('agent', 'Agent'),
    ], required=True, default='coworker',
        help='Hierarkisk nivå för målet. Company = företagsövergripande, '
             'Department = avdelningsmål, Coworker = per AI-medarbetare, '
             'Agent = per enskild agent.')

    sequence = fields.Integer(default=10)

    # Hierarki (cascade)
    parent_id = fields.Many2one('ai.org.goal', string='Parent Goal',
        index=True, ondelete='cascade')
    child_ids = fields.One2many('ai.org.goal', 'parent_id',
        string='Sub-Goals')

    # Kopplingar till organisationen
    company_id = fields.Many2one('res.company',
        default=lambda self: self.env.company)
    department_id = fields.Many2one('hr.department',
        string='Department',
        help='Avdelning som äger detta mål.')
    coworker_id = fields.Many2one('ai.coworker',
        string='Coworker',
        help='AI-medarbetare som äger detta mål.')
    agent_id = fields.Many2one('ai.agent',
        help='Specifik agent som äger detta mål.')

    # SMART
    specific = fields.Text('Specific — what exactly?')
    measurable = fields.Text('Measurable — how is it measured?')
    achievable = fields.Text('Achievable — is it realistic?')
    relevant = fields.Text('Relevant — why this goal?')
    deadline = fields.Date('Time-bound — deadline')

    # Key Results (OKR-style)
    key_result_ids = fields.One2many('ai.org.key_result', 'goal_id',
        string='Key Results')

    # Progress
    progress = fields.Float('Progress %', default=0.0,
        group_operator='avg', compute='_compute_progress', store=True,
        help='Automatiskt beräknad som snitt av alla key results. '
             'Kan överskridas manuellt.')

    status = fields.Selection([
        ('draft', 'Draft'),
        ('active', 'Active'),
        ('completed', 'Completed'),
        ('cancelled', 'Cancelled'),
    ], default='draft', required=True, index=True)

    # Metadata
    user_id = fields.Many2one('res.users', string='Owner',
        default=lambda self: self.env.user)
    is_favorite = fields.Boolean('Favorite')

    # Bridge — polymorf referens till externa modulers mål
    external_ref = fields.Reference(
        selection='_selection_external_refs',
        string='External Reference',
        help='Länk till externt mål (t.ex. strategy.initiative). '
             'Fylls i av bridge-moduler.')

    @api.depends('key_result_ids.current_value', 'key_result_ids.target_value')
    def _compute_progress(self):
        """Progress is average of all key results' completion %."""
        for goal in self:
            if not goal.key_result_ids:
                goal.progress = 0.0
                continue
            total_pct = 0.0
            count = 0
            for kr in goal.key_result_ids:
                if kr.target_value:
                    pct = (kr.current_value / kr.target_value) * 100.0
                    total_pct += min(pct, 100.0)
                    count += 1
            goal.progress = round(total_pct / count, 1) if count else 0.0

    @api.model
    def _selection_external_refs(self):
        """Dynamisk selection — bridge-moduler lägger till via inherit."""
        return []

    @api.constrains('parent_id')
    def _check_parent_level(self):
        """Parent must be at a higher level than child."""
        for goal in self:
            if goal.parent_id:
                levels = ['company', 'department', 'coworker', 'agent']
                parent_idx = levels.index(goal.parent_id.level)
                child_idx = levels.index(goal.level)
                if child_idx <= parent_idx:
                    raise models.ValidationError(_(
                        'Child goal level must be deeper than parent. '
                        '%s cannot have child of level %s.'
                    ) % (goal.parent_id.level, goal.level))

    def action_activate(self):
        self.write({'status': 'active'})

    def action_complete(self):
        self.write({'status': 'completed', 'progress': 100.0})

    def action_cancel(self):
        self.write({'status': 'cancelled'})
