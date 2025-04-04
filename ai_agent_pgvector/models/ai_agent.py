import logging
import json

from odoo import models, fields, api, _
from odoo.tools.safe_eval import safe_eval
from odoo.exceptions import ValidationError
  
_logger = logging.getLogger(__name__)

class AIAgent(models.Model):
    _inherit = 'ai.agent'
    
    ai_type = fields.Selection(selection_add=[('garantgruppen', 'Garantgruppen')],ondelete={'garantgruppen': 'cascade'})