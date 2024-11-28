
import os
from langchain_core.prompts import PromptTemplate
from langchain_openai import ChatOpenAI
from langchain_mistralai import ChatMistralAI
# from langchain_core.output_parsers import StrOutputParse

from odoo import models, fields, api, _
from odoo.exceptions import UserError, AccessError, ValidationError
import logging

_logger = logging.getLogger(__name__)

class AIAgent(models.Model):
    _name = 'ai.agent'
    _description = 'scaffold_test.scaffold_test'

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
            _logger.error(f"{record.create_agent(answer=answer,question=question)}")

    def create_agent(self,**kwargs):

        template_prompt = self._create_ai_templet_prompt(kwargs.keys())
        message = template_prompt.invoke(kwargs)
        answer = self.get_llm().invoke(message)
        return answer.content

    def get_llm(self):

        if self.ai_agent_llm_id:

            if self.ai_agent_llm_id.name == "ChatGPT":

                os.environ["OPENAI_API_KEY"] = self.ai_agent_llm_id.ai_api_key
                return ChatOpenAI(model="gpt-4o")

    def _create_ai_templet_prompt(self,*args):

        return PromptTemplate(template=self.ai_prompt_template,input_variables=args)
                






