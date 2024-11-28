# from langchain_core.prompts import PromptTemplate
# from langchain_openai import ChatOpenAI
# from langchain_mistralai import ChatMistralAI
# from langchain_core.output_parsers import StrOutputParse

from odoo import models, fields, api, _
from odoo.exceptions import UserError, AccessError, ValidationError
import logging

_logger = logging.getLogger(__name__)


class AIAgentModel(models.Model):
    _name = 'ai.agent.llm'
    _description = 'AI Agent LLM'

    name = fields.Char(required=True)
    is_key_required = fields.Boolean(default=True)
    ai_api_key = fields.Char()
