from odoo import models, fields, api, _
from odoo.exceptions import UserError, AccessError, ValidationError
import logging

_logger = logging.getLogger(__name__)

class AIQuestSessionLine(models.Model):
    _name = 'ai.quest.session.line'
    _description = 'AI Quest Session Line'
    _inherit = ["mail.thread", "mail.activity.mixin"]

    ai_quest_session_id = fields.Many2one(comodel_name="ai.quest.session")
    ai_id = fields.Char()
    input_tokens = fields.Integer()
    output_tokens = fields.Integer()
    total_tokens = fields.Integer()
    audio = fields.Integer()
    cache_read = fields.Integer()
    # audio = fields.Integer()
    reasoning = fields.Integer()
    model_name = fields.Char()
    system_fingerprint = fields.Char()
    finish_reason = fields.Char()
   

   