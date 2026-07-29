# -*- coding: utf-8 -*-
"""Extend calendar.event with AI goal linking (conditional)."""

from odoo import models, fields
import logging

_logger = logging.getLogger(__name__)

try:
    class CalendarEvent(models.Model):
        _inherit = 'calendar.event'

        ai_goal_id = fields.Many2one(
            'ai.personal.goal', string='AI Goal',
            help='Link this calendar event to a personal AI goal.')
except Exception:
    _logger.info('calendar.event not available — skipping AI goal integration')
