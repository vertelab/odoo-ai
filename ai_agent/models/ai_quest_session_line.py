from odoo import models, fields, api, _
from odoo.exceptions import UserError, AccessError, ValidationError
import logging

_logger = logging.getLogger(__name__)

class AIQuestSessionLine(models.Model):
    _name = 'ai.quest.session.line'
    _description = 'AI Quest Session Line'
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _rec_name = "display_name"

    datetime = fields.Datetime(string='Datetime',default=fields.Datetime.now()) # fields.datetime.add|context_timestamp|end_of|now|start_of|substract|to_datetime|to_string|today
    display_name = fields.Char(compute="compute_display_name")
    product_tmpl_id = fields.Many2one(comodel_name='product.template',string="",help="") # domain|context|ondelete="'set null', 'restrict', 'cascade'"|auto_join|delegate
    ai_quest_session_id = fields.Many2one(comodel_name="ai.quest.session")
    ai_quest_id = fields.Many2one(comodel_name="ai.quest", related="ai_quest_session_id.ai_quest_id",stored=True)
    ai_agent_id = fields.Many2one(comodel_name="ai.agent")
    ai_llm_id = fields.Many2one(comodel_name="ai.llm")
    model_id = fields.Many2one(comodel_name="product.attribute.value")
    api_type_id = fields.Many2one(comodel_name="product.attribute.value")
    data_type_id = fields.Many2one(comodel_name="product.attribute.value")

    token_type_id = fields.Many2one(comodel_name="product.attribute.value")    
    token = fields.Integer()
    token_currency = fields.Many2one(comodel_name='res.currency',string="Currency",help="") # domain|context|ondelete="'set null', 'restrict', 'cascade'"|auto_join|delegate
    token_monetary = fields.Monetary()
    token_sys = fields.Integer() # Compute, stored  llm_additional_rate * (token * pricelist | token_monetary(currency)) -> company_id.currency_id * 1000 billigate frågan = 1
    llm_additional_rate = fields.Float(string='Additional rate', related="product_tmpl_id.llm_additional_rate")

    system_fingerprint = fields.Char()
    finish_reason = fields.Char()

    @api.depends("model_name","ai_quest_session_id.session")
    def compute_display_name(self):
        for record in self:
            record.display_name = f"{record.model_name} [{record.ai_quest_session_id.session}]"
   
