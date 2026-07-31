# -*- coding: utf-8 -*-
"""hr.job extension — AI-medarbetare per roll."""

from odoo import models, fields


class Job(models.Model):
    _inherit = 'hr.job'

    ai_coworker_ids = fields.Many2many(
        'ai.coworker',
        'hr_job_ai_coworker_rel',
        'job_id', 'coworker_id',
        string='AI-medarbetare',
        help='AI-medarbetare som har denna roll.')
