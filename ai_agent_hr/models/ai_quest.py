
from odoo import models, api, fields, _
from odoo.exceptions import UserError, AccessError, ValidationError
import logging

_logger = logging.getLogger(__name__)


class AIAgent(models.Model):
    _inherit = "ai.agent"

    department_id = fields.Many2one(comodel_name='hr.department',)
    ai_type = fields.Selection(selection_add=[('oos', 'OOS')], ondelete={'oos': 'cascade'})



class AIQuest(models.Model):
    _inherit = "ai.quest"

    department_id = fields.Many2one(comodel_name='hr.department',)
    ai_type = fields.Selection(selection_add=[('oos', 'OOS')], ondelete={'oos': 'cascade'})

            

class AISession(models.Model):
    _inherit = "ai.quest.session"

    department_id = fields.Many2one(comodel_name='hr.department',related="ai_quest_id.department_id", store=True)
    ai_type = fields.Selection(selection_add=[('oos', 'OOS')], ondelete={'oos': 'cascade'})

            

class AISessionLine(models.Model):
    _inherit = "ai.quest.session.line"

    department_id = fields.Many2one(comodel_name='hr.department',related="ai_quest_session_id.ai_quest_id.department_id", store=True)


