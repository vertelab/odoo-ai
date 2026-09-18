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
