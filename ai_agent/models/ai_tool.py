import json
import importlib
import inspect
import sys
import traceback
from langgraph.graph import END, START, StateGraph, MessagesState
from random import randint
if sys.version_info >= (3, 12):
    from typing import Annotated, Literal, TypedDict, Sequence
else:
    from typing_extensions import Annotated, Literal, TypedDict, Sequence

from odoo import models, fields, api, _
from odoo.exceptions import UserError, AccessError, ValidationError

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
    company_id = fields.Many2one(comodel_name='res.company',string="Company",help="") # domain|context|ondelete="'set null', 'restrict', 'cascade'"|auto_join|delegate
    debug = fields.Boolean(string='Debug')
    image_128 = fields.Image("Image", max_width=128, max_height=128)
    is_favorite = fields.Boolean()
    last_run = fields.Datetime()
    name = fields.Char(required=True)
    quest_count = fields.Integer(compute="compute_quest_count")
    session_count = fields.Integer(compute="compute_session_count")
    session_line_count = fields.Integer(compute="compute_session_line_count")
    session_line_ids = fields.One2many(comodel_name="ai.quest.session.line", inverse_name="ai_tool_id")
    status = fields.Selection(
        selection=[("draft", "Draft"), ("active", "Active"), ("done", "Done"), ("error", "Error")],
        default="draft")
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
       
    def action_test_tool(self):
        action = {
            'name': 'Test Tool',
            'type': 'ir.actions.act_window',
            'res_model': 'ai.tool.test.wizard',
            'view_mode': 'form',
            'context': {'default_ai_tool_id': self.id},
            'target': 'new'
        }
        _logger.error(f"{action}")
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
            record.base_image_128 = record.image_128 or None

    def log_message(self, body, is_error=False):
        if is_error:
            self.status = "error"
        self.last_run = fields.Datetime.now()
        self.message_post(body=f"{body} | {self.last_run}", message_type="notification")


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

        _logger.error(f"{all_tools=}")

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
       
     
class AIToolTestWizard(models.Model):
    _name = 'ai.tool.test.wizard'
    _description = 'A testing wizard for ai tool'

    is_raise_error = fields.Boolean(default=True)
    test_tool_input = fields.Char()
    ai_tool_id = fields.Many2one(comodel_name="ai.tool")
    file = fields.Binary(attachment=True)
    filename = fields.Char('Filename')
    object_name = fields.Char("Object Name", help="The name that this object will have in state")
    object_id = fields.Reference(string='Object', selection=lambda m: [(model.model, model.name) for model in
                                                                       m.env['ir.model'].sudo().search([])])
    def test_tool(self):
        
        def create_args_dict(func):
            func_sig = inspect.signature(func)
            list_args = list(func_sig.parameters.keys())
            args_dict = {}
            for arg in list_args:
                args_dict.update({arg: self.test_tool_input})
            return args_dict
        
        results = ''
        state = {}
        if self.file:
            state.update({"attachments": [self.file]})
        if self.object_name:
            state.update({self.object_name: self.object_id})

        try:
            module = importlib.import_module(self.ai_tool_id.tool_lib)
            TOOL = getattr(module, self.ai_tool_id.tool)
            get_tool = TOOL(state)
            func = get_tool.func
            results = get_tool.run(create_args_dict(func))
        except ImportError as e:
            _logger.error(f"Error importing {self.ai_tool_id.tool_lib}: {e}")
        except AttributeError as e:
            _logger.error(f"Error: {e} {traceback.format_exc()}")
            _logger.error(f"Error: {self.ai_tool_id.tool} not found in {self.ai_tool_id.tool_lib}")
        except Exception as e:
            _logger.error(f"An error occurred: {e} {traceback.format_exc()}")
    
        if self.is_raise_error:
            raise UserError(f"{results=}")
        _logger.info(f"{results=}")
