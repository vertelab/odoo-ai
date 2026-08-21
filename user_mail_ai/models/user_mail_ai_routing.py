# -*- coding: utf-8 -*-
"""Specialist-routing: kategori → aktiv specialist-coworker (handoff).

Deklarativ (data XML) — bryggor lägger till rader, mönstret är samma som
graph.node.definition. user_mail_ai seedar faktura → Faktura-assistenten.
"""

import logging

from odoo import models, fields

_logger = logging.getLogger(__name__)


class UserMailAiRouting(models.Model):
    _name = 'user_mail_ai.routing'
    _description = 'Mail-routing: kategori → specialist-coworker'
    _order = 'sequence, id'

    category = fields.Selection([
        ('newsletter', 'Nyhetsbrev'),
        ('invoice', 'Faktura'),
        ('meeting_invite', 'Mötesinbjudan'),
        ('support', 'Support'),
        ('project', 'Projekt'),
        ('personal', 'Personlig'),
        ('other', 'Övrigt'),
    ], string='Kategori', required=True, index=True)
    coworker_id = fields.Many2one(
        'ai.coworker', string='Specialist', required=True,
        help='Aktiv specialist som tar emot handoff.')
    active = fields.Boolean(default=True)
    sequence = fields.Integer(default=10)

    _sql_constraints = [
        ('unique_category', 'unique(category)',
         'En routing per kategori!'),
    ]
