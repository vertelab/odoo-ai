from random import randint

from odoo import models, fields, api, _
from odoo.exceptions import UserError, AccessError, ValidationError
from odoo.tools.safe_eval import safe_eval
import logging

_logger = logging.getLogger(__name__)

class AIQuest(models.Model):
    _name = 'ai.quest'
    _inherit = ["mail.thread", "mail.activity.mixin", "mail.alias.mixin"]
    _description = 'AI Quest'

    name = fields.Char(required=True)
    description = fields.Text()
    ai_quest_session_ids = fields.One2many(comodel_name="ai.quest.session", inverse_name="ai_quest_id")
    status = fields.Selection(selection=[("draft",_("Draft")),("active",_("Active")),("done",_("Done")),("error",_("Error"))], default="draft")
    ai_type = fields.Selection(selection=[("default","Default")], default="default")
    color = fields.Integer(default=lambda self: randint(1, 11))
    is_favorite = fields.Boolean()

    def start(self):
        pass

        