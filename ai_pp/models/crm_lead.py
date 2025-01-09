from odoo import models, fields, api, _
from odoo.exceptions import UserError, AccessError, ValidationError

import logging

_logger = logging.getLogger(__name__)

class CRMLead(models.Model):
    _inherit = "crm.lead"

    tender_case_number = fields.Char()