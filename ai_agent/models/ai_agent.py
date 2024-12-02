import os

import os
import json
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
    ai_type = fields.Selection(selection=[("default", "Default")], default="default", required=True)

    def test(self):
        for record in self:
            ai_prompt_template = \
                """
            {answer}
            ==============
             Below this text, you have a question, and above you have the answer. Return the answer to the question.
            ==============
            {question} 
            """.strip()
            answer = "42"
            question = "what is the meaning of life the universe and everything?"
            raise UserError(
                f"{record.prompt_agent(prompt=ai_prompt_template, answer=answer, question=question)}"
            )

    def prompt_agent(self, prompt=False, partial_variables=False, **kwargs):
        try:
            response = self._instantiate_model().invoke(
                self._create_ai_template_prompt(prompt, partial_variables, **kwargs)
            )
            return response.content
        except Exception as e:
            raise UserError(e)

    def _create_ai_template_prompt(self, prompt, partial_variables=False, **kwargs):
        template = PromptTemplate(
            template=prompt or self.ai_prompt_template,
            input_variables=kwargs.keys(),
            partial_variables={"json_format": partial_variables}
        )
        message = template.invoke(kwargs)
        return message

    def _instantiate_model(self):
        if not self.ai_agent_llm_id:
            raise UserError(_(" "))
        llm_name = self.ai_agent_llm_id.name
        llm = getattr(langchain_openai, llm_name)(api_key=self.ai_agent_llm_id.ai_api_key, model="gpt-4o")
        return llm
