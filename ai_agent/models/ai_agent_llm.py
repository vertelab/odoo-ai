from random import randint

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
    llm_type = fields.Char(required=True)
    model = fields.Char()
    ai_api_key = fields.Char()
    status = fields.Selection(selection=[("not_confirmed", "Not Confirmed"),("confirmed", "Confirmed"),("error", "Error")], default="not_confirmed")
    status_color = fields.Integer(compute="compute_status_color")
    endpoint = fields.Char()
    ai_agent_ids = fields.One2many(comodel_name="ai.agent",inverse_name="ai_agent_llm_id")
    color = fields.Integer(default=lambda self: randint(1, 11))
    is_favorite = fields.Boolean()
    agent_count = fields.Integer(compute="compute_agent_count")
    last_run = fields.Datetime()
    product_tmpl_id = fields.Many2one('product.template')
    model_id = fields.Many2one('product.template.attribute.value', string="Model",
                               domain="[('product_tmpl_id', '=', product_tmpl_id)]")

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

    @api.depends("status")
    def compute_status_color(self):
        for record in self:
            record.status_color = 0
            if record.status == "not_confirmed":
                record.status_color = 3
            elif record.status == "confirmed":
                record.status_color = 10
            elif record.status == "error":
                record.status_color = 1

    @api.depends("ai_agent_ids")
    def compute_agent_count(self):
        for record in self:
            record.agent_count = len(record.ai_agent_ids)

    def log_message(self,body,is_error=False):
        if is_error:
            self.status = "error"
        self.last_run = fields.Datetime.now()
        self.message_post(body=f"{body} | {self.last_run}",message_type="notification")

    def get_llm(self):
        return f"{self.llm_type}(" + "model=" + "'" + f"{self.model if self.model else ''}" + "'" + "," + "api_key=" + "'" + f"{self.ai_api_key if self.ai_api_key else ''}" + "'" + ")"