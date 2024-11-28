import os

from langchain_core.prompts import PromptTemplate
import langchain_openai
from langchain_openai import ChatOpenAI
from langchain_mistralai import ChatMistralAI
# from langchain_core.output_parsers import StrOutputParse

from odoo import models, fields, api, _
from odoo.exceptions import UserError, AccessError, ValidationError
import logging

_logger = logging.getLogger(__name__)


class AIAgent(models.Model):
    _name = 'ai.agent'
    _description = 'AI Agent'

    name = fields.Char(required=True)
    ai_prompt_template = fields.Html()
    ai_agent_model_id = fields.Many2one(comodel_name="ai.agent.llm")

    def create_agent(self, **kwargs):
        template_prompt = self._create_ai_templet_prompt(kwargs.keys())
        message = template_prompt.invoke(kwargs)
        answer = self._instantiate_model().invoke(message)
        return answer.content

    def _instantiate_model(self):
        if not self.ai_agent_model_id:
            raise UserError(_(" "))
        llm = getattr(langchain_openai, self.name)(api_key=self.ai_agent_model_id.ai_api_key, model="gpt-4o")
        return llm

    def _create_ai_templet_prompt(self, *args):
        return PromptTemplate(template=self.ai_prompt_template, input_variables=args)
