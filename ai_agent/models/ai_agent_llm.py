from random import randint
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langchain_mistralai import ChatMistralAI


from httpx import HTTPStatusError

from odoo import models, fields, api, _
from odoo.exceptions import UserError, AccessError, ValidationError
import logging

_logger = logging.getLogger(__name__)

class AIAgentLLM(models.Model):
    _name = 'ai.agent.llm'
    _description = 'AI Agent LLM'
    _inherit = ["mail.thread", "mail.activity.mixin"]

    name = fields.Char(required=True)
    is_key_required = fields.Boolean(default=True)
    ai_api_key = fields.Char()
    status = fields.Selection(selection=[("not_confirmed", "Not Confirmed"),("confirmed", "Confirmed"),("error", "Error")], default="not_confirmed")
    status_color = fields.Integer(compute="compute_status_color")
    endpoint = fields.Char()
    ai_agent_ids = fields.One2many(comodel_name="ai.agent",inverse_name="ai_agent_llm_id")
    color = fields.Integer(default=lambda self: randint(1, 11))
    is_favorite = fields.Boolean()
    agent_count = fields.Integer(compute="compute_agent_count")
    last_run = fields.Datetime()
    product_tmpl_id = fields.Many2one(comodel_name='product.template',domain="[('is_llm','=',True)]", required=True)
    llm_type = fields.Char(related="product_tmpl_id.llm_type", required=True)
    model_id = fields.Many2one(comodel_name='product.template.attribute.value', string="Model", required=True, ) # domain="[('product_tmpl_id', '=', product_tmpl_id),('attribute_id','=', ref('ai_agent.open_ai_product_attribute_model'))]")
    ai_quest_session_ids = fields.One2many(comodel_name="ai.quest.session", inverse_name="ai_agent_llm_id")
    session_line_count = fields.Integer(compute="compute_session_line_count")


    def action_get_agents(self):
        action = {
            'name': 'AI Agents',
            'type': 'ir.actions.act_window',
            'res_model': 'ai.agent',
            'view_mode': 'kanban,tree,form',
            'target': 'current',
            'domain': [("id", 'in', self.ai_agent_ids.ids)]
        }
        return action

    def action_get_session_lines(self):
        action = {
            'name': 'Session Lines',
            'type': 'ir.actions.act_window',
            'res_model': 'ai.quest.session.line',
            'view_mode': 'tree,form',
            'target': 'current',
            'domain': [("ai_quest_session_id", 'in', self.ai_quest_session_ids.ids)]
        }
        return action

    def log_message(self,body,is_error=False):
        if is_error:
            self.status = "error"
        self.last_run = fields.Datetime.now()
        self.message_post(body=f"{body} | {self.last_run}",message_type="notification")

    def get_llm(self):
        return f"{self.llm_type}(" + "model=" + "'" + f"{self.model_id.name if self.model_id.name else ''}" + "'" + "," + "api_key=" + "'" + f"{self.ai_api_key if self.ai_api_key else ''}" + "'" + ")"

    def invoke(self,input,config=None,
                    ai_quest_session_id=None,ai_quest_id=None,ai_agent_id=None,debug=False,
                ):
        try:
            response = eval(self.get_llm()).invoke(input,config)            
        except HTTPStatusError as e:
            self.log_message(body=e,is_error=True)
            _logger.error(f"{e=}")
            return None
        except Exception as e:
            self.log_message(body=e,is_error=True)
            _logger.error(f"{e=}")
            return None
        
        content = response.content
        additional_kwargs = response.additional_kwargs
        response_metadata = response.response_metadata
        usage_metadata = dict(response_metadata.get('usage_metadata',{}))
        # ~ raise UserError(f"{response.usage_metadata=} {response.usage_metadata['input_tokens']=} ")

        for token_type,token in response.usage_metadata.items():
            _logger.error(f"{token_type=} {token=}")
            if token_type == 'total_tokens':
                next
            token_type_id = self.env['product.attribute.value'].search([('name','=',token_type)])
            # ~ if not token_type_id:
                # ~ pass
            self.env['ai.quest.session.line'].new_line(values=
                {
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
            )
            
        if debug:
            self.log_message(body="%s" % response,is_error=False)
        return content

    @api.depends("ai_quest_session_ids")
    def compute_session_line_count(self):
        for record in self:
            line_count = 0
            for session in record.ai_quest_session_ids:
                line_count += len(session.ai_quest_session_line_ids)
            record.session_line_count = line_count

    @api.depends("status")
    def compute_status_color(self):
        for record in self:
            record.status_color = 0
            if record.status == "not_confirmed":
                record.status_color = 3 # Orange
            elif record.status == "confirmed":
                record.status_color = 10 # Green
            elif record.status == "error":
                record.status_color = 1 # Red

    @api.depends("ai_agent_ids")
    def compute_agent_count(self):
        for record in self:
            record.agent_count = len(record.ai_agent_ids)




    def test_llm(self):        
        if self.invoke("""
                {"question": "what is the meaning of life the universe and everything?", "answer": 42}
                """,debug=True):
            self.status = "confirmed"
