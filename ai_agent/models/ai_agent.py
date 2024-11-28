import os

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
    ai_agent_llm_id = fields.Many2one(comodel_name="ai.agent.llm")

    def test(self):
        for record in self:
            record.ai_prompt_template = \
                """
            {answer}
            ==============
             Below this text, you have a question, and above you have the answer. Return the answer to the question.
            ==============
            {question} 
            """
            answer = "42"
            question = "what is the meaning of life the universe and everything?"
            _logger.error(f"{record.create_agent(answer=answer, question=question)}")

    def create_agent(self, **kwargs):

        template_prompt = self._create_ai_templet_prompt(kwargs.keys())
        message = template_prompt.invoke(kwargs)
        answer = self._instantiate_model().invoke(message)
        return answer.content

    def _create_ai_templet_prompt(self, *args):
        return PromptTemplate(template=self.ai_prompt_template, input_variables=args)

    def _instantiate_model(self):
        if not self.ai_agent_llm_id:
            raise UserError(_(" "))
        llm = getattr(langchain_openai, self.name)(api_key=self.ai_agent_llm_id.ai_api_key, model="gpt-4o")
        return llm

