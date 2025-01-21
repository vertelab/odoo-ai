# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.


import logging


from odoo import api, fields, models, tools, _
from odoo.exceptions import ValidationError, UserError


_logger = logging.getLogger(__name__)


class Company(models.Model):
    _inherit = "res.company"

    company_mission = fields.Html(string='Our Mission')
    company_values = fields.Html(string='Our values')
