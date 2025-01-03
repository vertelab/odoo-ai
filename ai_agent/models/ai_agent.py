import os
import json
from langchain_core.prompts import PromptTemplate
from langchain_openai import ChatOpenAI
from langchain_mistralai import ChatMistralAI
from langchain_groq import ChatGroq
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


    # ~ session_ids = fields.One2many(comodel_name="ai.quest.session", )
    ai_agent_data_ids = fields.One2many(comodel_name="ai.agent.data", inverse_name="agent_id")
    ai_agent_llm_id = fields.Many2one(comodel_name="ai.agent.llm", string="LLM", help="Choose Large Language Model",      
                                      domain="[('status','=','confirmed')]")
    ai_backstory = fields.Text(string="Backstory")
    ai_discription = fields.Text()
    ai_goal = fields.Text(string="Goal")
    ai_prompt_template = fields.Html()
    ai_role = fields.Char(string="Role")
    ai_temperature = fields.Float(string='temperature', default=0.7,                                  
            help="Temperature controls the randomness and creativity of the model's output, <1.0 more predictable and consistant "
                 ">1.0 more diverse and creative responses")
    ai_type = fields.Selection(selection=[("default", "Default")], default="default", required=True)
    color = fields.Integer(default=lambda self: randint(1, 11))
    debug = fields.Boolean(string='Debug')
    is_favorite = fields.Boolean()
    last_run = fields.Datetime()
    name = fields.Char(required=True)
    quest_count = fields.Integer(compute="compute_quest_count")
    quest_ids = fields.Many2many(comodel_name="ai.quest")
    session_count = fields.Integer(compute="compute_session_count")
    session_line_count = fields.Integer(compute="compute_session_line_count")
    session_line_ids = fields.One2many(comodel_name="ai.quest.session.line", inverse_name="ai_agent_id")
    status = fields.Selection(        
        selection=[("draft", _("Draft")), ("active", _("Active")), ("done", _("Done")), ("error", _("Error"))],        
        default="draft")
    tag_ids = fields.Many2many(comodel_name='product.tag', string='Tags')

    def action_get_quests(self):
        action = {
            'name': 'AI Quests',
            'type': 'ir.actions.act_window',
            'res_model': 'ai.quest',
            'view_mode': 'kanban,tree,form,calendar',
            'target': 'current',
            'domain': [("session_line_ids.ai_agent_id", '=', self.id)]
        }
        return action

    def action_get_session_lines(self):
        action = {
            'name': 'Session Lines',
            'type': 'ir.actions.act_window',
            'res_model': 'ai.quest.session.line',
            'view_mode': 'tree,form,calendar,pivot',
            'target': 'current',
            'domain': [("ai_agent_id", '=', self.id)],
        }
        return action

    def action_get_sessions(self):
        action = {
            'name': 'Sessions',
            'type': 'ir.actions.act_window',
            'res_model': 'ai.quest.session',
            'view_mode': 'tree,form,calendar',
            'target': 'current',
            'domain': [("session_line_ids.ai_agent_id", '=', self.id)]
        }
        return action

    @api.depends("session_line_ids")
    def compute_session_line_count(self):
        for record in self:
            record.session_line_count = len(record.session_line_ids)

    @api.depends("session_line_ids")
    def compute_session_count(self):
        for record in self:
            record.session_count = len(
                set(record.session_line_ids.filtered(lambda x: x.ai_agent_id.id == record.id).mapped(
                    'ai_quest_session_id')))

    @api.depends("session_line_ids")
    def compute_quest_count(self):
        for record in self:
            record.quest_count = len(
                set(record.session_line_ids.filtered(lambda x: x.ai_agent_id.id == record.id).mapped('ai_quest_id')))

    def prompt_agent(self, test_prompt=False, parser=False, session=False, **kwargs):
        self.last_run = fields.Datetime.now()
        _logger.error(f"{session.session=}")

        if not self.ai_agent_llm_id:
            raise UserError("No LLM")

        response = False

        try:
            response = eval(self.ai_agent_llm_id.get_llm()).invoke(
                self._create_ai_template_prompt(kwargs, test_prompt, parser)
            )

        except HTTPStatusError as e:
            self.ai_agent_llm_id.log_message(body=e, is_error=True)
            _logger.error(f"HTTPStatusError {e=}")
            self.ai_agent_llm_id.log_message(body=f"HTTPStatusError {e=}")
            self.status = self.ai_agent_llm_id.status = 'error'
            self.log_message(body=f"HTTPStatusError {e=}")

        except Exception as e:
            _logger.error(f"{e=}")
            self.ai_agent_llm_id.log_message(body=f" {e=}")
            self.log_message(body=f" {e=}")

        _logger.error(f"{response=}")
        self.ai_agent_llm_id.log_message(body="Success!!!")

        if response and session:
            session.ai_agent_llm_id = self.ai_agent_llm_id
            session.store_session_data(response)

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

    def test(self):
        self.last_run = fields.Datetime.now()

    def log_message(self, body, is_error=False):
        if is_error:
            self.status = "error"
        self.last_run = fields.Datetime.now()
        self.message_post(body=f"{body} | {self.last_run}", message_type="notification")
