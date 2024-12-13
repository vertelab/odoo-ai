from odoo import models, fields, api, _
from odoo.exceptions import UserError, AccessError, ValidationError
from odoo.tools.safe_eval import safe_eval
import logging

_logger = logging.getLogger(__name__)

class AIQuest(models.Model):
    _name = 'ai.quest'
    _inherit = ["mail.alias.mixin"]
    _description = 'AI Quest'

    name = fields.Char()
    description = fields.Text()
    ai_quest_session_ids = fields.One2many(comodel_name="ai.quest.session", inverse_name="ai_quest_id")
    status = fields.Selection(selection=[("draft","Draft"),("active","Active"),("done","Done")], default="draft")
    ai_type = fields.Selection(selection=[("default","Default")], default="default")

        