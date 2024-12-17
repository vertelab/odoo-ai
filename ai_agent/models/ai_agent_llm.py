# from langchain_core.prompts import PromptTemplate
# from langchain_openai import ChatOpenAI
# from langchain_mistralai import ChatMistralAI
# from langchain_core.output_parsers import StrOutputParse

from odoo import models, fields, api, _
from odoo.exceptions import UserError, AccessError, ValidationError
import logging

_logger = logging.getLogger(__name__)


class AIAgentLLM(models.Model):
    _name = 'ai.agent.llm'
    _description = 'AI Agent LLM'
    _inherit = ["mail.thread", "mail.activity.mixin"]

    name = fields.Char(required=True)
    is_key_required = fields.Boolean(default=True)
    llm_type = fields.Char(required=True)
    model = fields.Char()
    ai_api_key = fields.Char()
    status = fields.Selection(selection=[("default", "Default")], default="default")
    endpoint = fields.Char()
    product_tmpl_id = fields.Many2one('product.template')
    model_id = fields.Many2one('product.template.attribute.value', string="Model",
                               domain="[('product_tmpl_id', '=', product_tmpl_id)]")

    def get_llm(self):
        return f"{self.llm_type}(" + "model=" + "'" + f"{self.model if self.model else ''}" + "'" + "," + "api_key=" + "'" + f"{self.ai_api_key if self.ai_api_key else ''}" + "'" + ")"

    # def log_message(self,message):
    #     self.message_post(body=message)
