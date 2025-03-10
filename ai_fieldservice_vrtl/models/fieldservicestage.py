from odoo import api, fields, models, _
from datetime import date as date_type
from datetime import datetime, timedelta
import logging
_logger = logging.getLogger(__name__)


class FieldServiceStage(models.Model):
    _inherit = 'fieldservice.stage'

    start_quest = fields.Boolean()
