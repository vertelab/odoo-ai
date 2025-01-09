import os
import json
from langchain_core.prompts import PromptTemplate
from langchain.schema import SystemMessage, HumanMessage
from langchain_openai import ChatOpenAI
from langchain_mistralai import ChatMistralAI
from langchain_groq import ChatGroq
from langchain_anthropic import ChatAnthropic
from httpx import HTTPStatusError
from random import randint
from langchain_core.output_parsers import StrOutputParser


from odoo import models, fields, api, _
from odoo.exceptions import UserError, AccessError, ValidationError
from odoo.tools.safe_eval import safe_eval
import logging


_logger = logging.getLogger(__name__)

class AIAgentTool(models.Model):
    _name = 'ai.agent.tool'
    _description = 'AI Agent Tool'

    ai_agent_id = fields.Many2one(comodel_name='ai.agent', string="", help="")
    sequence = fields.Integer(string='Sequence')
    ai_tool_id = fields.Many2one(comodel_name='ai.tool', string="Tool", help="")



class AITool(models.Model):
    _name = 'ai.tool'
    _inherit = ["mail.thread", "mail.activity.mixin",]

    _description = 'AI Tool'

    color = fields.Integer(default=lambda self: randint(1, 11))
    debug = fields.Boolean(string='Debug')
    is_favorite = fields.Boolean()
    last_run = fields.Datetime()
    name = fields.Charrequired=True)
    status = fields.Selection(
        selection=[("draft", _("Draft")), ("active", _("Active")), ("done", _("Done")), ("error", _("Error"))],
        default="draft")
    tag_ids = fields.Many2many(comodel_name='product.tag', string='Tags')
    image_128 = fields.Image("Image", max_width=128, max_height=128)
    base_image_128 = fields.Image("Base Image", max_width=128, max_height=128, compute='_compute_base_image_128')
    tool = fields.Char(string='Tool', trim=True, )
    tool_lib = fields.Char(string='Library', trim=True, )
    tool_api_key = fields.Char(string='API-key', trim=True, )
    
    @api.depends('image_128')
    def _compute_base_image_128(self):
        for record in self:
            record.base_image_128 = record.image_128 or record.ai_agent_llm_id.image_128

    def log_message(self, body, is_error=False):
        if is_error:
            self.status = "error"
        self.last_run = fields.Datetime.now()
        self.message_post(body=f"{body} | {self.last_run}", message_type="notification")
