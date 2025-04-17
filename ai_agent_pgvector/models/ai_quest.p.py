import logging
import json

from odoo import models, fields, api, _
from odoo.tools.safe_eval import safe_eval
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)

class AiQuest(models.Model):
    _inherit = "ai.quest"

    use_feedback_history = fields.Boolean()
    feedback_llm = fields.Many2one(comodel_name="ai.agent.llm", domain=[("is_embedded","=",True)])




  
        