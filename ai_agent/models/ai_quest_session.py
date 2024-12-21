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
        usage_metadata = session.usage_metadata
        response_metadata = session.response_metadata
        usage_metadata_dict = self.unwrap_dict(dict(usage_metadata))

        model_id = self.env["product.attribute.value"].search([("name", "=", response_metadata["model_name"]),("attribute_id", "=", self.env.ref("ai_agent.open_ai_product_attribute_model").id)],limit=1)
        api_type_id = self.env["product.attribute.value"].search([("name", "=", "sync"),("attribute_id", "=", self.env.ref("ai_agent.open_ai_product_attribute_api_type").id)],limit=1)
        data_type_id = self.env["product.attribute.value"].search([("name", "=", "text"),("attribute_id", "=", self.env.ref("ai_agent.open_ai_product_attribute_data_type").id)],limit=1)
        token_type_ids = self.env["product.attribute.value"].search([("attribute_id", "=", self.env.ref("ai_agent.open_ai_product_attribute_token_type").id)])

        record = {
                    "product_tmpl_id": self.ai_agent_llm_id.product_tmpl_id.id,
                    "model_id": model_id.id, 
                    "api_type_id": api_type_id.id, 
                    "data_type_id": data_type_id.id, 
                    "finish_reason": response_metadata["finish_reason"],
                    "system_fingerprint": response_metadata["system_fingerprint"]
                }

        for token_type_id in token_type_ids:    
            search_term = f"{token_type_id.name}_tokens" if token_type_id.name != "input cached" else "cache_read"
            record.update({"token": usage_metadata_dict[search_term], "token_type_id": token_type_id.id})
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


