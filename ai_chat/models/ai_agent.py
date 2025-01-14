from random import randint
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langchain_mistralai import ChatMistralAI
from langchain.agents import AgentExecutor, create_openai_tools_agent, create_json_chat_agent, create_react_agent

from httpx import HTTPStatusError

from odoo import models, fields, api, _
from odoo.exceptions import UserError, AccessError, ValidationError
import logging

_logger = logging.getLogger(__name__)


class AIAgent(models.Model):
    _inherit = "ai.agent"

    ai_type = fields.Selection(selection_add=[('chat_rag', 'Chat RAG')], ondelete={'chat_rag': 'cascade'})
