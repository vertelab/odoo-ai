# -*- coding: utf-8 -*-
import logging
from odoo import models, fields, api, _

_logger = logging.getLogger(__name__)


class ResUsers(models.Model):
    _inherit = 'res.users'

    llm_type = fields.Selection(
        selection_add=[('openinterpreter', "Open Interpreter")], ondelete={'openinterpreter': 'set default'}
    )