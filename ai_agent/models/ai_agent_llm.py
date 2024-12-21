from random import randint
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

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
    model_id = fields.Many2one('product.template.attribute.value', string="Model", required=True)
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
