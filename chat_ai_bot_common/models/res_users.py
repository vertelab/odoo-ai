# -*- coding: utf-8 -*-
import logging
#import time
from odoo import models, fields, api, _

# Logger settings. In this module we set messages in green textcolor
_logger = logging.getLogger(__name__)
# Set textcolor into blue and backgroundcolor into white, must be head in message. (blu for blue)
blu = "\033[34;47m"
# Reset Color to default, must be tail in message. (cr for color reset)
c_reset = "\033[0m"


class ResUsers(models.Model):
    _inherit = 'res.users'

    # TODO driver type so we can install several and tie a bot to it
    
    is_ai_bot = fields.Boolean(string="Is an AI-Bot")
    openai_api_key = fields.Char(required=False)
    openai_base_url = fields.Char(required=False)
    openai_model = fields.Char(required=False)
    openai_max_tokens = fields.Integer(required=False)
    openai_temperature = fields.Float(string="Temperature", required=False)
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
    #alternative_llm_api_key = fields.Char(string="Alternative LLM API Key")
    #alternative_llm_url = fields.Char(string="Alternative LLM URL")
    #alternative_llm = fields.Selection([], string="Alternative LLM Model", default=False)
    #api_key_icon = fields.Char(string="API Key Icon", compute="_compute_api_key_icon")

    """
    #Use this if we not want to use the slider:
    
    temperatures = [
    (0.0, "0.0"),
    (0.1, "0.1"),
    (0.2, "0.2"),
    (0.3, "0.3"),
    (0.4, "0.4"),
    (0.5, "0.5"),
    (0.6, "0.6"),
    (0.7, "0.7"),
    (0.8, "0.8"),
    (0.9, "0.9"),
    (1.0, "1.0"),
    ]

    openai_temperature = fields.Selection(string="Temperature", selection=temperatures, required=False)
    """


    def run_ai_message_post(self, recipient, channel, author, message):
        _logger.warning(f"{blu}Chat AI Bot Common / res_users / run_ai_message_post: Enter{c_reset}")
        #_logger.warning("Common run_ai_message_post"*100)
        return False 
        ## Meant to be overridden


        #_logger.warning("run_ai_message_post start of chain of functions"*10)
        #""" Returns the form action URL, for form-based acquirer implementations. """
        #if hasattr(self, '%s_run_ai_message_post' % self.llm_type):
        #    return getattr(self, '%s_run_ai_message_post' % self.llm_type)(recipient, channel, author, message)
        #return False
