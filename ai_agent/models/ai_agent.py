
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
    ai_agent_model_id = fields.Many2one(comodel_name="ai.agent.llm")

    # def create_agent(self,**kwargs):

    #     template_prompt = self._create_ai_templet_prompt(kwargs.keys())

    #     message = template_prompt.invoke(kwargs)

    # def _create_ai_templet_prompt(self,*args):

    #     return PromptTemplate(template=self.ai_prompt_template,input_variables=args)
                






