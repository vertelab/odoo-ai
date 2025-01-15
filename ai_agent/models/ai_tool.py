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
from langchain_community.tools import DuckDuckGoSearchRun, DuckDuckGoSearchResults
from langchain_community.tools.wikipedia.tool import WikipediaQueryRun
from langchain_community.tools.yahoo_finance_news import YahooFinanceNewsTool
from langchain_community.utilities.duckduckgo_search import DuckDuckGoSearchAPIWrapper
from langchain_community.utilities.wikipedia import WikipediaAPIWrapper
from langgraph.graph import END, START, StateGraph, MessagesState
from typing import Annotated, Literal, TypedDict, Sequence

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
    _inherit = ["mail.thread", "mail.activity.mixin", ]

    _description = 'AI Tool'

    ai_agent_count = fields.Integer(compute="compute_ai_agent_count")
    ai_agent_ids = fields.One2many(comodel_name="ai.agent.tool", inverse_name="ai_tool_id")
    base_image_128 = fields.Image("Base Image", max_width=128, max_height=128, compute='_compute_base_image_128')
    color = fields.Integer(default=lambda self: randint(1, 11))
    debug = fields.Boolean(string='Debug')
    image_128 = fields.Image("Image", max_width=128, max_height=128)
    is_favorite = fields.Boolean()
    last_run = fields.Datetime()
    name = fields.Char(required=True)
    quest_count = fields.Integer(compute="compute_quest_count")
    session_count = fields.Integer(compute="compute_session_count")
    session_line_count = fields.Integer(compute="compute_session_line_count")
    session_line_ids = fields.One2many(comodel_name="ai.quest.session.line", inverse_name="ai_tool_id")
    status = fields.Selection(selection=[("draft", _("Draft")), ("active", _("Active")), ("done", _("Done")), ("error", _("Error"))],default="draft")
    tag_ids = fields.Many2many(comodel_name='product.tag', string='Tags')
    tool = fields.Char(string='Tool', trim=True, )
    tool_api_key = fields.Char(string='API-key', trim=True, )
    tool_lib = fields.Char(string='Library', trim=True, )

    def action_get_quests(self):
        action = {
            'name': 'AI Quests',
            'type': 'ir.actions.act_window',
            'res_model': 'ai.quest',
            'view_mode': 'kanban,tree,form',
            'target': 'current',
            'domain': [("session_line_ids.ai_tool_id", '=', self.id)]
        }
        return action

    def action_get_agents(self):
        action = {
            'name': 'AI Agents',
            'type': 'ir.actions.act_window',
            'res_model': 'ai.agent',
            'view_mode': 'kanban,tree,form',
            'target': 'current',
            'domain': [("session_line_ids.ai_tool_id", '=', self.id)]
        }
        return action

    def action_get_session_lines(self):
        action = {
            'name': 'Session Lines',
            'type': 'ir.actions.act_window',
            'res_model': 'ai.quest.session.line',
            'view_mode': 'tree,form,calendar,pivot',
            'target': 'current',
            'domain': [("ai_tool_id", '=', self.id)],
        }
        return action

    def action_get_sessions(self):
        action = {
            'name': 'Sessions',
            'type': 'ir.actions.act_window',
            'res_model': 'ai.quest.session',
            'view_mode': 'tree,form',
            'target': 'current',
            'domain': [("session_line_ids.ai_tool_id", '=', self.id)]
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
                set(record.session_line_ids.filtered(lambda x: x.ai_tool_id.id == record.id).mapped(
                    'ai_quest_session_id')))

    @api.depends("session_line_ids")
    def compute_quest_count(self):
        for record in self:
            record.quest_count = len(
                set(record.session_line_ids.filtered(lambda x: x.ai_tool_id.id == record.id).mapped('ai_quest_id')))

    @api.depends("ai_agent_ids")
    def compute_ai_agent_count(self):
        for record in self:
            record.ai_agent_count = len(record.ai_agent_ids)



    @api.depends('image_128')
    def _compute_base_image_128(self):
        for record in self:
            record.base_image_128 = record.image_128 or record.ai_agent_llm_id.image_128

    def log_message(self, body, is_error=False):
        if is_error:
            self.status = "error"
        self.last_run = fields.Datetime.now()
        self.message_post(body=f"{body} | {self.last_run}", message_type="notification")


    # ~ @tool
    # ~ def weather_tool(query: str):
        # ~ """Get weather information."""
        # ~ if "sf" in query.lower() or "san francisco" in query.lower():
            # ~ return "It's 60 degrees and foggy."
        # ~ return "It's 90 degrees and sunny."

    # ~ @tool
    # ~ def search_tool(query: str):
        # ~ """Search for information."""
        # ~ return f"Found results for: {query}"

    # ~ @tool
    # ~ def search_duck_tool(query: str):
        # ~ """Search for information on duckduck."""
        # ~ search = DuckDuckGoSearchResults()
        # ~ return search


    # ~ def _get_alias_model_name(self):
        # ~ return 'ai.quest'

    # ~ @api.model
    # ~ def _get_alias_values(self):
        # ~ values = super(AIQuest, self)._get_alias_values()
        # ~ values['alias_model_id'] = self.env['ir.model']._get('ai.quest').id
        # ~ return values

    # ~ def start(self):
        # ~ self.run()

    # ~ @tool
    # ~ def weather_tool(query: str):
        # ~ """Get weather information."""
        # ~ if "sf" in query.lower() or "san francisco" in query.lower():
            # ~ return "It's 60 degrees and foggy."
        # ~ return "It's 90 degrees and sunny."

    # ~ @tool
    # ~ def search_tool(query: str):
        # ~ """Search for information."""
        # ~ return f"Found results for: {query}"

    # ~ @tool
    # ~ def search_duck_tool(query: str):
        # ~ """Search for information on duckduck."""
        # ~ search = DuckDuckGoSearchResults()
        # ~ return search

    def should_continue(self, state: MessagesState) -> Literal["tools", END]:
        messages = state['messages']
        last_message = messages[-1]
        # If the LLM makes a tool call, then we route to the "tools" node
        if last_message.tool_calls:
            return "tools"
        # Otherwise, we stop (reply to the user)
        return END

    def get_tools(self, tool_names=None):
        # Get all methods ending with _tool
        all_tools = [getattr(self, attr) for attr in dir(self) if attr.endswith('_tool')]

        if not tool_names:
            return all_tools

        if isinstance(tool_names, str):
            tool_names = [tool_names]

        return [getattr(self, f"{name}_tool") for name in tool_names]

    def call_model(self, state: MessagesState):
        messages = state['messages']

        # Get tools for this instance
        x_tools = self.get_tools()
        # Invoke model
        response = eval(
            self.ai_agent_ids[0].ai_agent_id.ai_agent_llm_id.get_llm()
        ).bind_tools(x_tools).invoke(messages)

        _logger.info(f"{response.content=}")

        return {"messages": [response]}
