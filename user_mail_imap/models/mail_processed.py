# -*- coding: utf-8 -*-
"""Beständig dedup för IMAP-pollern — processade Message-ID:n per användare."""

import logging

from odoo import models, fields

_logger = logging.getLogger(__name__)


class UserMailProcessed(models.Model):
    _name = 'user.mail.processed'
    _description = 'Processed Mail Message-ID (poller dedup)'
    _order = 'processed_at desc'

    user_id = fields.Many2one(
        'res.users', string='Användare', required=True,
        ondelete='cascade', index=True)
    message_id = fields.Char(
        string='Message-ID / dedup-nyckel', required=True, index=True)
    processed_at = fields.Datetime(
        string='Processad', default=fields.Datetime.now)

    _sql_constraints = [
        ('unique_user_message',
         'unique(user_id, message_id)',
         'Message already processed for this user!'),
    ]
