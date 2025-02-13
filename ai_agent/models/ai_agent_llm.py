from httpx import HTTPStatusError
from langchain.agents import AgentExecutor, create_openai_tools_agent, create_json_chat_agent, create_react_agent
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.utils.utils import convert_to_secret_str
from langchain_mistralai import ChatMistralAI
from langchain_openai import ChatOpenAI
from odoo import models, fields, api, tools, _
from odoo.exceptions import UserError, AccessError, ValidationError
from random import randint
import importlib
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
    tag_ids = fields.Many2many(comodel_name='product.tag', string='Tags')

    def action_get_quests(self):
        action = {
            'name': 'AI Quests',
            'type': 'ir.actions.act_window',
            'res_model': 'ai.quest',
            'view_mode': 'kanban,tree,form',
            'target': 'current',
            'domain': [("session_line_ids.ai_llm_id", '=', self.id)]
        }
        return action

    def action_get_agents(self):
        action = {
            'name': 'AI Agents',
            'type': 'ir.actions.act_window',
            'res_model': 'ai.agent',
            'view_mode': 'kanban,tree,form',
            'target': 'current',
            'domain': [("session_line_ids.ai_llm_id", '=', self.id)]
        }
        return action

    def action_get_session_lines(self):
        action = {
            'name': 'Session Lines',
            'type': 'ir.actions.act_window',
            'res_model': 'ai.quest.session.line',
            'view_mode': 'tree,form,calendar,pivot',
            'target': 'current',
            'domain': [("ai_llm_id", '=', self.id)],
        }
        return action

    def action_get_sessions(self):
        action = {
            'name': 'Sessions',
            'type': 'ir.actions.act_window',
            'res_model': 'ai.quest.session',
            'view_mode': 'tree,form',
            'target': 'current',
            'domain': [("session_line_ids.ai_llm_id", '=', self.id)]
        }
        return action

    @api.depends("session_line_ids")
    def compute_session_line_count(self):
        for record in self:
            record.session_line_count = sum([l.token_sys or 0 for l in record.session_line_ids])

    @api.depends("session_line_ids")
    def compute_session_count(self):
        for record in self:
            record.session_count = len(
                set(record.session_line_ids.filtered(lambda x: x.ai_llm_id.id == record.id).mapped(
                    'ai_quest_session_id')))

    @api.depends("session_line_ids")
    def compute_quest_count(self):
        for record in self:
            record.quest_count = len(
                set(record.session_line_ids.filtered(lambda x: x.ai_llm_id.id == record.id).mapped('ai_quest_id')))

    @api.depends("ai_agent_ids")
    def compute_ai_agent_count(self):
        for record in self:
            record.ai_agent_count = len(record.ai_agent_ids)

    def log_message(self, body, is_error=False):
        if is_error:
            self.status = "error"
        self.last_run = fields.Datetime.now()
        self.message_post(body=f"{body} | {self.last_run}", message_type="notification")

    def get_llm(self, verbose=False, temperature=0.7, callbacks=None, **kwarg):
        try:
            module = importlib.import_module(self.product_tmpl_id.llm_library)
            LLM = getattr(module, self.product_tmpl_id.llm_type)
            #_logger.warning(f"{LLM=}")
            if self.product_tmpl_id.llm_type == "AzureChatOpenAI":
               kwarg['api_version'] = self.api_version
               kwarg['azure_endpoint'] = self.azure_endpoint
            api_key = self.ai_api_key
            if not api_key:
                api_key = tools.config.get(self.product_tmpl_id.fallback_api_key_name, False)
                _logger.error(f"{api_key=}")
            return LLM(verbose=verbose, temperature=temperature, callbacks=callbacks, api_key=self.ai_api_key,
                       model=self.model_id.name, **kwarg)
        except ImportError as e:
            _logger.error(f"Error importing {self.product_tmpl_id.llm_library}: {e}")
            raise
        except AttributeError as e:
            _logger.error(f"Error: {self.product_tmpl_id.llm_type} not found in {self.product_tmpl_id.llm_library}")
            raise
        except Exception as e:
            _logger.error(f"An error occurred: {e}")
            raise


    def get_embedding(self):

        try:
            module = importlib.import_module(self.product_tmpl_id.llm_library)
            LLM = getattr(module, self.product_tmpl_id.llm_etype)
            return LLM(api_key=self.ai_api_key, model=self.model_id.name)
        except ImportError as e:
            _logger.error(f"Error importing {self.product_tmpl_id.llm_library}: {e}")
            raise
        except AttributeError as e:
            _logger.error(f"Error: {self.product_tmpl_id.llm_etype} not found in {self.product_tmpl_id.llm_library}")
            raise
        except Exception as e:
            _logger.error(f"An error occurred: {e}")
            raise

    def invoke(self, input, config=None, ai_quest_session_id=None, ai_quest_id=None, ai_agent_id=None, debug=False):
        try:
            response = eval(self.get_llm()).invoke(input, config)
        except HTTPStatusError as e:
            self.log_message(body=e, is_error=True)
            _logger.error(f"{e=}")
            return None
        except Exception as e:
            self.log_message(body=e, is_error=True)
            _logger.error(f"{e=}")
            return None

        content = response.content
        additional_kwargs = response.additional_kwargs
        response_metadata = response.response_metadata
        usage_metadata = dict(response_metadata.get('usage_metadata', {}))

        for token_type, token in response.usage_metadata.items():
            _logger.error(f"{token_type=} {token=}")
            if token_type == 'total_tokens':
                next
            token_type_id = self.env['product.attribute.value'].search([('name', '=', token_type)])
            self.env['ai.quest.session.line'].new_line(
                values={
                    'ai_quest_session_id': ai_quest_session_id,
                    'ai_quest_id': ai_quest_id,
                    'ai_agent_id': ai_agent_id,
                    'ai_llm_id': self.id,
                    'product_tmpl_id': self.product_tmpl_id.id,
                    'model_id': self.model_id.id,
                    'model_real': response_metadata.get('model'),
                    'api_type_id': None,
                    'data_type_id': None,
                    'token_type_id': token_type_id.id if token_type_id else None,
                    'token': token,
                    'system_fingerprint': response.id,
                    'finish_reason': response_metadata.get('finish_reason'),
                }



        if debug:
            self.log_message(body="%s" % response, is_error=False)
        return content

    @api.depends("status")
    def compute_status_color(self):
        for record in self:
            record.status_color = 0
            if record.status == "not_confirmed":
                record.status_color = 3  # Orange
            elif record.status == "confirmed":
                record.status_color = 10  # Green
            elif record.status == "error":
                record.status_color = 1  # Red

    def test_llm(self):
        session = self.env['ai.quest.session'].llm_init(self)
        session.state = 'done'
        self.status = "confirmed"

    def get_agent_executor(self, prompt, tools, temperature=1.0, verbose=False, callbacks=None):
        return AgentExecutor(
            agent=create_openai_tools_agent(eval(self.get_llm(temperature=temperature, verbose=verbose)), tools,
                                            prompt),
            tools=tools,
            verbose=verbose,
            callbacks=callbacks,
        )

    def update_api_key(self):
        for llm in self:
            llm.ai_api_key = llm.product_tmpl_id.ai_api_key


