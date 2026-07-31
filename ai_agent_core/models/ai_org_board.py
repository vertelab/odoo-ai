# -*- coding: utf-8 -*-
"""AI Organization Board — styrelsefunktion med beslutslogg."""

from odoo import models, fields, api, _


class AIOrgBoard(models.Model):
    _name = 'ai.org.board'
    _description = 'AI Organization Board'
    _rec_name = 'name'
    _order = 'name'

    name = fields.Char(required=True)
    description = fields.Text()

    company_id = fields.Many2one('res.company',
        default=lambda self: self.env.company)

    member_ids = fields.Many2many('ai.agent',
        'ai_org_board_agent_rel',
        'board_id', 'agent_id',
        string='AI Board Members')
    human_member_ids = fields.Many2many('res.users',
        'ai_org_board_user_rel',
        'board_id', 'user_id',
        string='Human Board Members')
    decision_ids = fields.One2many('ai.org.board.decision', 'board_id',
        string='Decisions')
    decision_count = fields.Integer(
        compute='_compute_decision_count')

    @api.depends('decision_ids')
    def _compute_decision_count(self):
        for board in self:
            board.decision_count = len(board.decision_ids)


class AIOrgBoardDecision(models.Model):
    _name = 'ai.org.board.decision'
    _description = 'AI Board Decision'
    _rec_name = 'title'
    _order = 'create_date desc'

    board_id = fields.Many2one('ai.org.board', string='Board',
        required=True, ondelete='cascade')

    title = fields.Char(required=True)
    description = fields.Text()

    proposed_by = fields.Reference(
        selection=[('ai.agent', 'AI Agent'), ('res.users', 'User')],
        string='Proposed By')

    status = fields.Selection([
        ('proposed', 'Proposed'),
        ('voting', 'Voting'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
        ('executed', 'Executed'),
    ], default='proposed', required=True)

    votes_for = fields.Integer(default=0)
    votes_against = fields.Integer(default=0)
    votes_abstain = fields.Integer(default=0)
    total_members = fields.Integer(compute='_compute_total_members')

    decision_date = fields.Datetime()

    # Executed result
    executed_action = fields.Text('Executed Action')

    @api.depends('board_id.member_ids', 'board_id.human_member_ids')
    def _compute_total_members(self):
        for d in self:
            board = d.board_id
            d.total_members = (len(board.member_ids)
                               + len(board.human_member_ids))

    def action_approve(self):
        self.write({
            'status': 'approved',
            'decision_date': fields.Datetime.now(),
        })

    def action_reject(self):
        self.write({
            'status': 'rejected',
            'decision_date': fields.Datetime.now(),
        })

    def action_execute(self):
        self.write({
            'status': 'executed',
            'decision_date': fields.Datetime.now(),
        })
