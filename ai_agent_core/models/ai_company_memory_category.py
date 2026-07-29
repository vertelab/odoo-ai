# -*- coding: utf-8 -*-
"""ai.company.memory.category — Kategorier med access-grupper för company memory."""

import logging
from odoo import models, fields, api, _

_logger = logging.getLogger(__name__)


class AICompanyMemoryCategory(models.Model):
    _name = 'ai.company.memory.category'
    _description = 'Company Memory Category'
    _order = 'name asc'
    _rec_name = 'name'

    name = fields.Char(string='Name', required=True,
                       help='Kebab-case identifier, e.g. "customer-intelligence"')
    description = fields.Text(string='Description',
                               help='What this category contains')
    group_ids = fields.Many2many(
        'res.groups', string='Access Groups',
        help='User groups with access to this category. '
             'Empty = public (all company users can see).')
    active = fields.Boolean(string='Active', default=True)

    @api.model
    def get_default_categories(self):
        """Seed data for default categories."""
        return [
            {'name': 'customer', 'description': 'Customer intelligence'},
            {'name': 'supplier', 'description': 'Supplier intelligence'},
            {'name': 'strategy', 'description': 'Strategy, OKR, BMC, SWOT'},
            {'name': 'marketing', 'description': 'Marketing campaigns, KPIs'},
            {'name': 'competitor', 'description': 'Competitor intelligence'},
            {'name': 'market', 'description': 'Market intelligence'},
            {'name': 'management', 'description': 'Management system, ISO'},
            {'name': 'knowledge', 'description': 'Knowledge base articles'},
            {'name': 'website', 'description': 'Website content'},
            {'name': 'social', 'description': 'Social media'},
            {'name': 'hr', 'description': 'HR data'},
            {'name': 'finance', 'description': 'Financial data'},
            {'name': 'mgmt_summary', 'description': 'Management summary'},
            {'name': 'operations', 'description': 'Operations data'},
        ]
