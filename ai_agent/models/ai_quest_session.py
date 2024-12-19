import uuid

from odoo import models, fields, api, _
from odoo.exceptions import UserError, AccessError, ValidationError
import logging

_logger = logging.getLogger(__name__)

class AIQuestSession(models.Model):
    _name = 'ai.quest.session'
    _description = 'AI Quest Session'
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _rec_name = "session"

    ai_quest_id = fields.Many2one(comodel_name="ai.quest")
    status = fields.Selection(selection=[("draft",_("Draft")),("active",_("Active")),("done",_("Done")),("error",_("Error"))], default="draft")
    session = fields.Char(default=lambda self: str(uuid.uuid4()))
    ai_type = fields.Selection(selection=[("default","Default")], default="default")
    ai_agent_ids = fields.Many2many(comodel_name="ai.agent")
    ai_agent_llm_id = fields.Many2one(comodel_name="ai.agent.llm")
    ai_quest_session_line_ids = fields.One2many(comodel_name="ai.quest.session.line", inverse_name="ai_quest_session_id")
    startdate = fields.Datetime()
    enddate = fields.Datetime()
    type_of_output = fields.Text()

    def store_session_data(self,session):
        response_metadata = session.response_metadata
        usage_metadata = session.usage_metadata
        record = self.unwrap_dict(dict(usage_metadata))
        record.update({"model_name": response_metadata["model_name"], "finish_reason": response_metadata["finish_reason"], "ai_id": session.id})
        self.create_ai_quest_session_line(record)

    def unwrap_dict(self,val_dict):
        new_dict = {}
        for key, value in val_dict.items():
            if type(value) == dict:
                new_dict.update(self.unwrap_dict(value))
                continue 
            new_dict[key] = value
        return new_dict

    def create_ai_quest_session_line(self,record):
        ai_quest_session_line_id = self.env["ai.quest.session.line"].create(record)
        self.ai_quest_session_line_ids = [(4,ai_quest_session_line_id.id)]


