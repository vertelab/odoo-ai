# -*- coding: utf-8 -*-
"""AI Organization Tasks — persisterande arbetsuppgifter med checkout_lock."""

import logging
from datetime import datetime
from odoo import models, fields, api, _

_logger = logging.getLogger(__name__)


TASK_SOURCES = [
    ('manual', 'Manual'),
    ('server_action', 'Server Action'),
    ('watch', 'Automation'),
    ('cron', 'Scheduled'),
    ('channel', 'Discuss'),
    ('web_ui', 'Web Chat'),
    ('onboarding', 'Onboarding'),
    ('kaizen', 'Kaizen'),
    ('board', 'Board Decision'),
]


class AIOrgTask(models.Model):
    _name = 'ai.org.task'
    _description = 'AI Organization Task'
    _rec_name = 'name'
    _order = 'priority desc, create_date asc'

    name = fields.Char(required=True)
    description = fields.Text()

    # Vem
    coworker_id = fields.Many2one('ai.coworker', string='Assignee Coworker',
        index=True,
        help='AI-medarbetaren som ska utföra uppgiften.')
    beställare_ref = fields.Reference(
        selection=[('res.users', 'User'), ('ai.agent', 'AI Agent')],
        string='Requester',
        help='Vem som beställde uppgiften.')

    # Källa
    source = fields.Selection(TASK_SOURCES, default='manual')

    # Status & kö
    status = fields.Selection([
        ('todo', 'Todo'),
        ('in_progress', 'In Progress'),
        ('review', 'Review'),
        ('done', 'Done'),
        ('blocked', 'Blocked'),
        ('cancelled', 'Cancelled'),
    ], default='todo', required=True, index=True)

    priority = fields.Selection([
        ('0', 'Low'),
        ('1', 'Normal'),
        ('2', 'High'),
        ('3', 'Urgent'),
    ], default='1', index=True)

    # Checkout (atomiskt — förhindrar dubbelarbete)
    checkout_lock = fields.Boolean(default=False,
        help='När True är uppgiften utcheckad av en coworker.')
    checked_out_at = fields.Datetime()
    checked_out_by = fields.Many2one('ai.coworker',
        string='Checked Out By')

    # Relationer
    session_ids = fields.One2many('ai.coworker.session', 'task_id',
        string='Sessions')
    goal_id = fields.Many2one('ai.org.goal', string='Goal',
        help='Målet denna uppgift bidrar till.')

    # Beroenden
    blocker_ids = fields.Many2many('ai.org.task',
        'ai_org_task_blocker_rel',
        'task_id', 'blocker_id',
        string='Blocked By',
        help='Andra tasks som måste vara klara innan denna.')

    # Work products
    work_product_ids = fields.Many2many('ir.attachment',
        string='Work Products',
        help='Filer/dokument som producerats.')

    # HR-koppling
    job_id = fields.Many2one('hr.job', string='Job Position',
        help='Roll i organisationen som denna task tillhör.')

    # Resultat
    result_summary = fields.Text('Result Summary')
    completed_at = fields.Datetime()

    # Metadata
    company_id = fields.Many2one('res.company',
        default=lambda self: self.env.company)
    active = fields.Boolean(default=True)

    def action_checkout(self):
        """Checka ut denna task — atomiskt."""
        self.ensure_one()
        if self.checkout_lock:
            raise models.ValidationError(_(
                'Task "%s" is already checked out.') % self.name)
        self.write({
            'checkout_lock': True,
            'checked_out_at': fields.Datetime.now(),
            'status': 'in_progress',
        })
        # Skapa session automatiskt
        session = self.env['ai.coworker.session'].create({
            'coworker_id': self.coworker_id.id,
            'task_id': self.id,
            'name': f'Task: {self.name[:50]}',
            'status': 'active',
        })
        _logger.info('Task %s checked out by coworker %s, session %s',
                     self.name, self.coworker_id.name, session.id)
        return session

    def action_checkin(self, result_summary=''):
        """Checka in task — markera som klar."""
        self.ensure_one()
        self.write({
            'checkout_lock': False,
            'status': 'done',
            'completed_at': fields.Datetime.now(),
            'result_summary': result_summary or self.result_summary,
        })
        _logger.info('Task %s checked in (done)', self.name)

    def action_cancel(self):
        self.write({'status': 'cancelled', 'checkout_lock': False})
