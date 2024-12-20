import os
import json
from langchain_core.prompts import PromptTemplate
from langchain_openai import ChatOpenAI
from langchain_mistralai import ChatMistralAI
from httpx import HTTPStatusError
from random import randint

# from langchain_core.output_parsers import StrOutputParse

from odoo import models, fields, api, _
from odoo.exceptions import UserError, AccessError, ValidationError
from odoo.tools.safe_eval import safe_eval
import logging

_logger = logging.getLogger(__name__)

class AIAgent(models.Model):
    _name = 'ai.agent'
    _description = 'AI Agent'
    _inherit = ["mail.thread", "mail.activity.mixin"]

    name = fields.Char(required=True)
    color = fields.Integer(default=lambda self: randint(1, 11))
    is_favorite = fields.Boolean()
    status = fields.Selection(selection=[("draft",_("Draft")),("active",_("Active")),("done",_("Done")),("error",_("Error"))], default="draft")
    ai_prompt_template = fields.Html()
    ai_agent_llm_id = fields.Many2one(comodel_name="ai.agent.llm")
    ai_type = fields.Selection(selection=[("default", "Default")], default="default", required=True)
    ai_discription = fields.Text()
    ai_role = fields.Char()
    ai_goal = fields.Text()
    ai_backstory = fields.Text()
    ai_quest_session_ids = fields.Many2many(comodel_name="ai.quest.session")
    ai_agent_data_ids = fields.One2many(comodel_name="ai.agent.data", inverse_name="agent_id")

    def prompt_agent(self, test_prompt=False, parser=False, session=False, **kwargs):

        _logger.error(f"{session.session=}")

        if not self.ai_agent_llm_id:
            raise UserError("No LLM")

        response = False

        try:
            response = eval(self.ai_agent_llm_id.get_llm()).invoke(
                self._create_ai_template_prompt(kwargs, test_prompt, parser)
            )
            
        except HTTPStatusError as e:
            self.ai_agent_llm_id.log_message(body=e,is_error=True)
            _logger.error(f"{e=}")

        except Exception as e:
            _logger.error(f"{e=}")

        _logger.error(f"{response=}")
        self.ai_agent_llm_id.log_message(body="Success!!!")

        if response and session:
            session.ai_agent_llm_id = self.ai_agent_llm_id
            session.store_session_data(response)
            self.ai_quest_session_ids = [(4, session.id, 0)]

        return response.content if response else ""
        
    def _create_ai_template_prompt(self, kwargs, test_prompt=False, parser=False, ):

        template = PromptTemplate(
            template=test_prompt or self.ai_prompt_template,
            input_variables=kwargs.keys(),
            partial_variables={"format_instructions": parser.get_format_instructions() if parser else False}
        )
        message = template.invoke(kwargs)
        return message

    def get_test_wizard(self):
        action = self.env.ref("ai_agent.action_ai_agent_test_wizard").read()[0]
        _logger.error(f"{action=}")
        action["context"] = {"default_ai_agent_id": self.id}
        return action
