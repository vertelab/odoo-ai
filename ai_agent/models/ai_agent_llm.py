from random import randint
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langchain_mistralai import ChatMistralAI
from langchain.agents import AgentExecutor, create_openai_tools_agent, create_json_chat_agent, create_react_agent
from langchain_core.utils.utils import convert_to_secret_str

from httpx import HTTPStatusError
import importlib

from odoo import models, fields, api, _
from odoo.exceptions import UserError, AccessError, ValidationError
import logging

_logger = logging.getLogger(__name__)

LICENCES = [
    ('ai-sweden-llm-ai-model', "AI Sweden's LLM AI Model License Agreement"),
    ('apache-2.0', 'Apache 2.0 License'),
    ('bigcode-open-rail-m-v1', 'BigCode Open RAIL-M v1 License Agreement'),
    ('commercial', 'Commercial License'),
    ('gemma-terms-of-use', 'Gemma Terms of Use'),
    ('google-ai-terms', 'Google AI-terms'),
    ('llama-community', 'Llama Community License'),
    ('mistral-research', 'Mistral Research License'),
    ('mit', 'MIT License'),
]


class AIAgentLLM(models.Model):
    _name = 'ai.agent.llm'
    _description = 'AI Agent LLM'
    _inherit = ["mail.thread", "mail.activity.mixin"]

    ai_agent_count = fields.Integer(compute="compute_ai_agent_count")
    ai_agent_ids = fields.One2many(comodel_name="ai.agent", inverse_name="ai_agent_llm_id")
    ai_api_key = fields.Char(default=lambda self: self.product_tmpl_id.ai_api_key)
    color = fields.Integer(default=lambda self: randint(1, 11))
    endpoint = fields.Char()
    image_128 = fields.Image("Image", max_width=128, max_height=128, related="product_tmpl_id.image_128")
    is_embedded = fields.Boolean(related='model_id.product_attribute_value_id.is_embedded')
    is_favorite = fields.Boolean()
    is_key_required = fields.Boolean(default=True)
    last_run = fields.Datetime()
    licence = fields.Selection(selection=LICENCES, string='Licence',
                               related='model_id.product_attribute_value_id.licence')
    llm_etype = fields.Char(related="product_tmpl_id.llm_etype", required=True)
    llm_type = fields.Char(related="product_tmpl_id.llm_type", required=True)
    model_id = fields.Many2one(comodel_name='product.template.attribute.value', string="Model", required=True, )
    name = fields.Char(required=True)
    product_tmpl_id = fields.Many2one(comodel_name='product.template', string="Provider",
                                      domain="[('is_llm','=',True)]", required=True)
    quest_count = fields.Integer(compute="compute_quest_count")
    session_count = fields.Integer(compute="compute_session_count")
    session_line_count = fields.Integer(compute="compute_session_line_count")
    session_line_ids = fields.One2many(comodel_name="ai.quest.session.line", inverse_name="ai_llm_id")
    status = fields.Selection(
        selection=[("not_confirmed", "Not Confirmed"), ("confirmed", "Confirmed"), ("error", "Error")],
        default="not_confirmed")
    status_color = fields.Integer(compute="compute_status_color")
