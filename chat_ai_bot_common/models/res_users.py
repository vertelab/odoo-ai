# -*- coding: utf-8 -*-
import logging
import time
from odoo import models, fields, api, _

_logger = logging.getLogger(__name__)


class ResUsers(models.Model):
    _inherit = 'res.users'

    # TODO driver type so we can install several and tie a bot to it
    is_ai_bot = fields.Boolean(string="Is an AI-Bot")
    openai_api_key = fields.Char(required=False)
    openai_base_url = fields.Char(required=False)
    openai_model = fields.Char(required=False)
    openai_max_tokens = fields.Integer(required=False)
    openai_temperature = fields.Float(required=False)
    # ~ chat_method = fields.Selection(required=False)
    chat_system_message = fields.Text(required=False)
    openai_prompt_prefix = fields.Char(required=False)
    openai_prompt_suffix = fields.Char(required=False)
    openai_assistant_name = fields.Char(required=False)
    openai_assistant_instructions = fields.Text(required=False)
    openai_assistant_tools = fields.Json(required=False)
    openai_assistant_model = fields.Char(required=False)
    openai_assistant = fields.Char(required=False)
    llm_type = fields.Selection([], string="LLM Type", default=False)

    def run_ai_message_post(self, recipient, channel, author, message):
        _logger.warning("run_ai_message_post start of chain of functions"*10)
        """ Returns the form action URL, for form-based acquirer implementations. """
        if hasattr(self, '%s_run_ai_message_post' % self.llm_type):
            return getattr(self, '%s_run_ai_message_post' % self.llm_type)(recipient, channel, author, message)
        return False
