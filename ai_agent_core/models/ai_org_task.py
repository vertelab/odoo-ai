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
    _inherit = ['mail.thread', 'mail.activity.mixin']
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

    # Delegering (coworker-delegation): ett uppdrag som en coworker lagt hos
    # en kollega via delegate_task. beställare_trust och delegation_depth
    # frystes vid skapandet och kan inte ändras av mottagaren.
    beställare_trust = fields.Integer(
        'Requester Trust Step', readonly=True, default=0,
        help='Beställarens tillitssteg när uppdraget skapades. Uppdraget '
             'utförs under min(beställare, utförare) — delegering ger ingen '
             'ny befogenhet. Readonly: sätts av delegate_task.')
    delegation_depth = fields.Integer(
        'Delegation Depth', readonly=True, default=0,
        help='Antal delegeringsled bakåt (beställarens djup + 1). Skyddar '
             'mot kedjor som växer obegränsat. Readonly: sätts av '
             'delegate_task.')
    parent_task_id = fields.Many2one(
        'ai.org.task', string='Delegated From',
        index=True, ondelete='set null',
        help='Uppgiften som delegerade denna (task-trädet). Gör att en '
             'delegeringskedjas samlade kostnad kan följas upp.')
    delegated_task_ids = fields.One2many(
        'ai.org.task', 'parent_task_id', string='Delegated Tasks')

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
    session_ids = fields.One2many('ai.coworker.session', 'ai_task_id',
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
    department_id = fields.Many2one(
        'hr.department', string='Department',
        compute='_compute_department', store=True,
        help='Avdelning — härleds från målet (goal_id) eller rollen (job_id). '
             'Gör att Tasks kan knytas till en avdelning i org-dashboarden.')

    @api.depends('goal_id.department_id', 'job_id.department_id')
    def _compute_department(self):
        for rec in self:
            rec.department_id = (
                rec.goal_id.department_id or rec.job_id.department_id
            )

    # Resultat
    result_summary = fields.Text('Result Summary')
    completed_at = fields.Datetime()

    # Metadata
    company_id = fields.Many2one('res.company',
        default=lambda self: self.env.company)
    active = fields.Boolean(default=True)

    def action_checkout(self, init_type='cron', job_prompt=None):
        """Checka ut denna task — ATOMISKT (bevakning-over-tid D4).

        Villkorad UPDATE (`WHERE checkout_lock = FALSE`) i stället för
        search+write: bara en villkorad UPDATE är atomisk under samtidighet.
        Två samtidiga heartbeat-anrop mot samma lediga uppgift ska ge exakt
        en lyckad utcheckning.

        Misslyckas session-skapandet frigörs uppgiften (D5) — en uppgift får
        aldrig lämnas utcheckad utan körning.
        """
        self.ensure_one()
        self.env.cr.execute("""
            UPDATE ai_org_task
               SET checkout_lock = TRUE,
                   checked_out_at = NOW(),
                   status = 'in_progress'
             WHERE id = %s AND checkout_lock = FALSE
        """, (self.id,))
        if self.env.cr.rowcount == 0:
            raise models.ValidationError(_(
                'Task "%s" is already checked out.') % self.name)
        self.invalidate_recordset(['checkout_lock', 'checked_out_at', 'status'])

        # Skapa session automatiskt — frigör uppgiften om det misslyckas.
        try:
            session = self.env['ai.coworker.session'].create({
                'coworker_id': self.coworker_id.id,
                'ai_task_id': self.id,
                'init_type': init_type,
                'name': f'Task: {self.name[:50]}',
                'status': 'active',
                'job_prompt': job_prompt or False,
                'heartbeat_pending': bool(job_prompt),
            })
        except Exception:
            # D5: frigör uppgiften — den får inte lämnas utcheckad utan körning.
            self.action_release()
            raise
        _logger.info('Task %s checked out by coworker %s, session %s',
                     self.name, self.coworker_id.name, session.id)
        return session

    def action_release(self):
        """Frigör en utcheckad uppgift utan körning (D5)."""
        self.ensure_one()
        self.write({
            'checkout_lock': False,
            'checked_out_at': False,
            'status': 'todo',
        })
        _logger.info('Task %s frigjord (ingen körning köades)', self.name)
        return True

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
