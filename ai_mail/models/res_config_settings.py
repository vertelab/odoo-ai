# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

from datetime import timedelta
from dateutil.relativedelta import relativedelta

from odoo import api, exceptions, fields, models, _


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

