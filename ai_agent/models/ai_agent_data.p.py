from odoo import models, fields, api, _
from odoo.exceptions import UserError, AccessError, ValidationError
from odoo.tools.safe_eval import safe_eval
import logging

_logger = logging.getLogger(__name__)


class AIAgentData(models.Model):
    _name = 'ai.agent.data'
    _description = 'AI Agent Data'

    agent_id = fields.Many2one(comodel_name="ai.agent")
    session = fields.Char()
    ai_agent_data_type = fields.Selection(selection=[("default", "Default")], default="default")
    data = fields.Binary()
    json = fields.Text()
    data_uuid = fields.Char()
