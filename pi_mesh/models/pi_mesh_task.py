# -*- coding: utf-8 -*-
"""
pi.mesh.task — arbete som delegerats mellan agenter.

Fas 5 handlar om arbetsfördelning. Modellen finns redan nu eftersom
meddelanden behöver kunna peka på en uppgift — och för att städningen
ska kunna skydda meddelanden som hör till pågående arbete.
"""

import logging

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class PiMeshTask(models.Model):
    _name = 'pi.mesh.task'
    _description = 'Uppgift delegerad mellan Pi-agenter'
    _order = 'create_date desc, id desc'

    name = fields.Char('Uppgift', required=True)
    description = fields.Text('Beskrivning')
    assigned_to = fields.Char('Tilldelad', index=True)
    assigned_by = fields.Char('Tilldelad av')
    status = fields.Selection(
        [('pending', 'Väntar'),
         ('assigned', 'Tilldelad'),
         ('running', 'Körs'),
         ('done', 'Klar'),
         ('failed', 'Misslyckad'),
         ('cancelled', 'Avbruten')],
        'Status', default='pending', index=True,
    )
    result = fields.Text('Resultat')
    started_at = fields.Datetime('Startad')
    finished_at = fields.Datetime('Klar')
    duration_minutes = fields.Float('Varaktighet (min)', compute='_compute_duration')
    message_ids = fields.One2many('pi.mesh.message', 'task_id', 'Meddelanden')
    message_count = fields.Integer('Meddelanden', compute='_compute_message_count')

    # ── Koppling till Odoos uppgiftsbegrepp (beslut 2026-09-18: väg c) ──
    #
    # VARFÖR ai.org.task och inte project.task:
    #   ai.org.task är byggd för att en COWORKER utför uppgiften —
    #   checkout skapar en session, coworkern kör, checkin skriver
    #   resultatet. Det är exakt vad meshen gör. project.task är byggd
    #   för att en MÄNNISKA utför den, och hade dessutom krävt att hela
    #   project-modulen installerades i en driftcontainer för att meshen
    #   ville ha en länk.
    #
    # OBS: ai.org.task är TOM i drift (0 rader) — bara onboarding skapar
    # den. Den här kopplingen är därför den första riktiga användningen.
    # Fältet är frivilligt: meshen fungerar utan Odoo (specens degraderade
    # läge), så en uppgift får inte kräva en Odoo-post.
    ai_task_id = fields.Many2one(
        'ai.org.task', 'Odoo-uppgift', ondelete='set null', index=True,
        help='Kopplingen till Odoos uppgiftsbegrepp. Sätts när en agent '
             'delegerar arbete som en coworker ska utföra.',
    )

    def action_link_ai_task(self):
        """Skapa (eller hitta) en ai.org.task och koppla den hit.

        Idempotent: en uppgift som redan är kopplad rörs inte.
        """
        AiTask = self.env['ai.org.task'].sudo()
        for task in self:
            if task.ai_task_id:
                continue
            task.ai_task_id = AiTask.create({
                'name': task.name,
                'description': task.description,
                'source': 'manual',
                'status': self._ai_status(task.status),
            })
        return True

    @api.model
    def _ai_status(self, mesh_status):
        """Översätt mesh-status till ai.org.task-status.

        Två statusmaskiner möts. Att mappa explicit i stället för att
        gissa gör översättningen synlig — och gör det möjligt att se när
        de glider ifrån varandra.
        """
        return {
            'pending': 'todo',
            'assigned': 'todo',
            'running': 'in_progress',
            'done': 'done',
            'failed': 'blocked',
            'cancelled': 'cancelled',
        }.get(mesh_status, 'todo')

    def write(self, vals):
        # Sätt sluttiden när uppgiften avslutas — annars är finished_at
        # False i samma ögonblick som raden nedan läser det, och
        # completed_at blir aldrig satt. (Fynd 2026-09-18: action_done()
        # satte båda, men write({'status': 'done'}) gjorde det inte —
        # och webhooken använder write.)
        if (vals.get('status') in ('done', 'failed', 'cancelled')
                and 'finished_at' not in vals):
            vals = dict(vals, finished_at=fields.Datetime.now())
        res = super().write(vals)
        # Håll Odoo-uppgiften i takt med meshen. Utan detta blir
        # kopplingen en engångsspegel: den skapas och sedan glider de
        # två statusarna ifrån varandra utan att någon märker det.
        if 'status' in vals:
            for task in self.filtered('ai_task_id'):
                task.ai_task_id.write({
                    'status': self._ai_status(task.status),
                    'result_summary': task.result,
                    'completed_at': task.finished_at
                    if task.status in ('done', 'failed', 'cancelled')
                    else False,
                })
        return res

    @api.depends('started_at', 'finished_at')
    def _compute_duration(self):
        for task in self:
            if task.started_at and task.finished_at:
                task.duration_minutes = (
                    task.finished_at - task.started_at
                ).total_seconds() / 60.0
            else:
                task.duration_minutes = 0.0

    @api.depends('message_ids')
    def _compute_message_count(self):
        for task in self:
            task.message_count = len(task.message_ids)

    def action_done(self):
        for task in self:
            task.write({
                'status': 'done',
                'finished_at': fields.Datetime.now(),
            })

    def action_failed(self):
        for task in self:
            task.write({
                'status': 'failed',
                'finished_at': fields.Datetime.now(),
            })
