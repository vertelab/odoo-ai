from odoo import models, fields, api, _
from odoo.exceptions import UserError, AccessError, ValidationError
import logging

_logger = logging.getLogger(__name__)

class AIQuestSessionLine(models.Model):
    _name = 'ai.quest.session.line'
    _description = 'AI Quest Session Line'
    # ~ _inherit = ['ai.quest.session.line',"mail.thread", "mail.activity.mixin"]
    # ~ _rec_name = "display_name"

    ai_agent_id = fields.Many2one(comodel_name="ai.agent")
    ai_llm_id = fields.Many2one(comodel_name="ai.agent.llm")
    ai_quest_id = fields.Many2one(comodel_name="ai.quest")
    ai_quest_session_id = fields.Many2one(comodel_name="ai.quest.session")
    api_type_id = fields.Many2one(comodel_name="product.attribute.value")
    commercial_partner_id = fields.Many2one(comodel_name='res.partner',string="Partner")
    data_type_id = fields.Many2one(comodel_name="product.attribute.value")
    datetime = fields.Datetime(string='Datetime',default=fields.Datetime.now()) # fields.datetime.add|context_timestamp|end_of|now|start_of|substract|to_datetime|to_string|today
    db_name = fields.Char(string='Database Name')
    db_uuid = fields.Char(string='Database UUID')
    display_name = fields.Char(compute="compute_display_name")
    finish_reason = fields.Char()
    llm_additional_rate = fields.Float(string='Additional rate', related="product_tmpl_id.llm_additional_rate")
    model_id = fields.Many2one(comodel_name="product.attribute.value")
    model_real = fields.Char()
    product_tmpl_id = fields.Many2one(comodel_name='product.template', string="",help="") # domain|context|ondelete="'set null', 'restrict', 'cascade'"|auto_join|delegate
    run_id = fields.Char()
    system_fingerprint = fields.Char()
    token = fields.Integer()
    token_currency = fields.Many2one(comodel_name='res.currency',string="Currency",help="") # domain|context|ondelete="'set null', 'restrict', 'cascade'"|auto_join|delegate
    token_monetary = fields.Monetary(currency_field="token_currency")
    token_sys = fields.Integer() # Compute, stored  llm_additional_rate * (token * pricelist | token_monetary(currency)) -> company_id.currency_id * 1000 billigate frågan = 1
    token_type_id = fields.Many2one(comodel_name="product.attribute.value")    
    user_id = fields.Many2one(comodel_name='res.users',string="User",help="")

    @api.model
    def new_line(self,session,aimessage,agent=None, debug=False):
        usage_metadata = aimessage.usage_metadata
        response_metadata = aimessage.response_metadata
        usage_metadata_dict = self.unwrap_dict(dict(usage_metadata))

        # ~ model_id = self.env["product.attribute.value"].search([("name", "=", response_metadata["model_name"]),("attribute_id", "=", self.env.ref("ai_agent.open_ai_product_attribute_model").id)],limit=1)
        api_type_id = self.env["product.attribute.value"].search([("name", "=", "sync"),("attribute_id", "=", self.env.ref("ai_agent.product_attribute_api_type").id)],limit=1)
        data_type_id = self.env["product.attribute.value"].search([("name", "=", "text"),("attribute_id", "=", self.env.ref("ai_agent.product_attribute_data_type").id)],limit=1)
        token_type_ids = self.env["product.attribute.value"].search([("attribute_id", "=", self.env.ref("ai_agent.product_attribute_token_type").id)])

        for token_type_id in token_type_ids:    
            search_term = f"{token_type_id.name}_tokens" if token_type_id.name != "input cached" else "cache_read"
            record = {
                "ai_agent_id": agent.id if agent else (session.ai_agent_id.id if session and session.ai_agent_id else None),
                "ai_llm_id":   agent.ai_agent_llm_id.id if agent and agent.ai_agent_llm_id else (session.ai_agent_llm_id.id if session.ai_agent_llm_id else None),
                "ai_quest_id": session.ai_quest_id.id if session.ai_quest_id else None,
                "ai_quest_session_id": session.id,
                "api_type_id": api_type_id.id,
                "commercial_partner_id": session.commercial_partner_id.id,
                "data_type_id": data_type_id.id,
                "db_name":     session.db_name,
                "db_uuid":     session.db_uuid,
                "finish_reason": response_metadata["finish_reason"],            
                "model_id":     agent.ai_agent_llm_id.model_id.id if agent else session.ai_agent_llm_id.model_id.id , 
                "model_real":   aimessage.model_name,
                "product_tmpl_id": session.ai_agent_llm_id.product_tmpl_id.id,
                "run_id":       aimessage.id,
                "system_fingerprint": response_metadata["system_fingerprint"],
                "token_type_id":token_type_id.id,
                "user_id":      session.user_id.id,
                'token':        usage_metadata_dict[search_term], 
            }
            line = self.create(record)
            if debug:
                session.log(llm,f"[session] line {line.name=} {record=}")
        
    def unwrap_dict(self,val_dict):
        new_dict = {}
        for key, value in val_dict.items():
            if type(value) == dict:
                new_dict.update(self.unwrap_dict(value))
                continue 
            new_dict[key] = value
        return new_dict
    
    @api.depends("model_id","ai_quest_session_id.session")
    def compute_display_name(self):
        for record in self:
            record.display_name = f"[{record.ai_quest_session_id.session}] {record.datetime}"
  
