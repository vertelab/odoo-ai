from random import randint
import re
import unidecode
import base64
import json
import markdown
from pytz import timezone
from functools import partial
from odoo import models, fields, api, _, tools, Command

from odoo.exceptions import UserError, AccessError, ValidationError, Warning
from odoo.tools.safe_eval import safe_eval, test_python_expr
from odoo.tools.float_utils import float_compare

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import PromptTemplate

from typing import Annotated, Literal, TypedDict

from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI
from langchain_anthropic import ChatAnthropic
from langchain_core.tools import tool
from langchain_community.tools import DuckDuckGoSearchRun, DuckDuckGoSearchResults

from langchain_community.utilities.duckduckgo_search import DuckDuckGoSearchAPIWrapper
from langchain_community.tools.yahoo_finance_news import YahooFinanceNewsTool
from langchain_community.utilities.wikipedia import WikipediaAPIWrapper
from langchain_community.tools.wikipedia.tool import WikipediaQueryRun
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph, MessagesState
from langgraph.prebuilt import ToolNode

import logging

_logger = logging.getLogger(__name__)


class AIQuestAgent(models.Model):
    _name = 'ai.quest.agent'
    _description = 'AI Quest AGent'

    ai_quest_id = fields.Many2one(comodel_name='ai.quest', string="", help="")
    sequence = fields.Integer(string='Sequence')
    ai_agent_id = fields.Many2one(comodel_name='ai.agent', string="Agent", help="")


# https://readmedium.com/langgraph-made-easy-a-beginners-guide-part-2-196e8b179119

DEFAULT_PYTHON_CODE = """# Available variables:
#  - env: Odoo Environment on which the action is triggered
#  - model: Odoo Model of the record on which the action is triggered; is a void recordset
#  - record: record on which the action is triggered; may be void
#  - records: recordset of all records on which the action is triggered in multi-mode; may be void
#  - time, datetime, dateutil, timezone: useful Python libraries
#  - float_compare: Odoo function to compare floats based on specific precisions
#  - log: log(message, level='info'): logging function to record debug information in ir.logging table
#  - UserError: Warning Exception to use with raise
#  - Command: x2Many commands namespace
# To return an action, assign: action = {...}\n\n\n\n"""


# Python code


class AIQuest(models.Model):
    _name = 'ai.quest'
    _inherit = ["mail.thread", "mail.activity.mixin", "mail.alias.mixin"]
    _description = 'AI Quest'

    ai_agent_ids = fields.One2many(comodel_name='ai.quest.agent', inverse_name='ai_quest_id', string="",
                                   help="")  # domain|context|auto_join|limit
    agent_count = fields.Integer(compute="compute_agent_count")
    ai_type = fields.Selection(selection=[("default", "Default")], default="default")
    color = fields.Integer(default=lambda self: randint(1, 11))
    description = fields.Text()
    init_type = fields.Selection(
        selection=[('manual', 'Manual'), ('mail', 'Mail'), ('chat', 'Chat with User'), ('channel', 'Chat with Channel'),
                   ('cron', 'Scheduled Action'), ('server-action', 'Server Action')], string='Initiate',
        help="How the Quest is initialized", required=True, default='manual')
    is_favorite = fields.Boolean()
    last_run = fields.Datetime()
    llm_count = fields.Integer(compute="compute_llm_count")
    name = fields.Char(required=True)
    server_action_id = fields.Many2one('ir.actions.server', string='Server Action',
                                       help="Server action to be executed when this quest is initialized",
                                       ondelete="cascade")
    session_count = fields.Integer(compute="compute_session_count")
    session_ids = fields.One2many(comodel_name="ai.quest.session", inverse_name="ai_quest_id")
    session_line_count = fields.Integer(compute="compute_session_line_count")
    session_line_ids = fields.One2many(comodel_name="ai.quest.session", inverse_name="ai_quest_id")
    status = fields.Selection(
        selection=[("draft", _("Draft")), ("active", _("Active")), ("done", _("Done")), ("error", _("Error"))],
        default="draft")

    alias_id = fields.Many2one(comodel_name='mail.alias', string='Alias', ondelete="restrict", required=True,
                               help="The email address associated with this channel. New emails received will automatically create new leads assigned to the channel.")
    alias_user_id = fields.Many2one(comodel_name='res.users', related='alias_id.alias_user_id', readonly=False,
                                    inherited=True, )  # ~ domain=lambda self: [('groups_id', 'in', self.env.ref('sales_team.group_sale_salesman_all_leads').id)]

    cron_id = fields.Many2one(comodel_name='ir.cron', string="Scheduled Action", help="",
                              ondelete="cascade")  # domain|context|ondelete="'set null', 'restrict', 'cascade'"|auto_join|delegate
    model_id = fields.Many2one(comodel_name='ir.model', string="Model",
                               help="Bind this Quest to yhis model")  # domain|context|ondelete="'set null', 'restrict', 'cascade'"|auto_join|delegate

    code = fields.Text(string='Python Code', groups='base.group_system',
                       default=DEFAULT_PYTHON_CODE,
                       help="Write Python code that the action will execute. Some variables are "
                            "available for use; help about python expression is given in the help tab.")
    channel_id = fields.Many2one(comodel_name='mail.channel', string="Channel", help="")
    chat_user_id = fields.Many2one(comodel_name='res.users', string="Chat User", help="")

    filter_domain = fields.Char(
        string='Filter Name',
        related='model_id.model', readonly=False, related_sudo=True)
    tag_ids = fields.Many2many(comodel_name='product.tag', string='Tags')
    user_id = fields.Many2one(comodel_name='res.users',string="Owner",help="") 
    partner_id = fields.Many2one(comodel_name='res.partner',string="Customer",help="") 


    def _get_alias_model_name(self):
        return 'ai.quest'

    @api.model
    def _get_alias_values(self):
        values = super(AIQuest, self)._get_alias_values()
        values['alias_model_id'] = self.env['ir.model']._get('ai.quest').id
        return values

    def start(self):
        self.run()

    @tool
    def weather_tool(query: str):
        """Get weather information."""
        if "sf" in query.lower() or "san francisco" in query.lower():
            return "It's 60 degrees and foggy."
        return "It's 90 degrees and sunny."

    @tool
    def search_tool(query: str):
        """Search for information."""
        return f"Found results for: {query}"

    @tool
    def search_duck_tool(query: str):
        """Search for information on duckduck."""
        search = DuckDuckGoSearchResults()
        return search

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

    # def get_tools(self, tool_name=None):
    #     # Initialize DuckDuckGo wrapper with explicit parameters
    #     search_wrapper = DuckDuckGoSearchAPIWrapper(
    #         max_results=2,
    #         time='d',  # last 24h
    #         backend='api'
    #     )
    #     search = DuckDuckGoSearchRun(api_wrapper=search_wrapper)
    #     wikipedia = WikipediaQueryRun(api_wrapper=WikipediaAPIWrapper())
    #
    #     community_tools = {
    #         'search': search,
    #         'wikipedia': wikipedia
    #     }
    #
    #     if tool_name:
    #         return [community_tools.get(tool_name)]
    #     return list(community_tools.values())

    def call_model(self, state: MessagesState):
        messages = state['messages']

        print("messages", messages)

        # Get tools for this instance
        x_tools = self.get_tools()
        # Invoke model
        response = eval(
            self.ai_agent_ids[0].ai_agent_id.ai_agent_llm_id.get_llm()
        ).bind_tools(x_tools).invoke(messages)

        print(response.content)

        _logger.info(f"{response.content=}")

        return {"messages": [response]}

    @api.depends('session_line_ids')
    def compute_llm_count(self):
        for record in self:
            record.llm_count = len(set(record.session_line_ids.mapped('ai_agent_llm_id')))

    def action_get_llms(self):
        action = {
            'name': 'LLMs',
            'type': 'ir.actions.act_window',
            'res_model': 'ai.agent.llm',
            'view_mode': 'kanban,tree,form,calendar',
            'target': 'current',
            'domain': [("session_line_ids.ai_quest_id", '=', self.id)]
        }
        return action

    def action_get_session_lines(self):
        action = {
            'name': 'Session Lines',
            'type': 'ir.actions.act_window',
            'res_model': 'ai.quest.session.line',
            'view_mode': 'tree,form',
            'target': 'current',
            'domain': [("ai_quest_id", '=', self.id)]
        }
        return action

    def action_get_sessions(self):
        action = {
            'name': 'Sessions',
            'type': 'ir.actions.act_window',
            'res_model': 'ai.quest.session',
            'view_mode': 'tree,form',
            'target': 'current',
            'domain': [("ai_quest_id", '=', self.id)]
        }
        return action

    def action_get_agents(self):
        action = {
            'name': 'AI Agents',
            'type': 'ir.actions.act_window',
            'res_model': 'ai.agent',
            'view_mode': 'kanban,tree,form',
            'target': 'current',
            'domain': [("session_line_ids.ai_quest_id", '=', self.id)]
        }
        return action

    @api.depends("session_line_ids")
    def compute_session_line_count(self):
        for record in self:
            record.session_line_count = len(record.session_line_ids)

    @api.depends("session_ids")
    def compute_session_count(self):
        for record in self:
            record.session_count = len(record.session_ids)

    @api.depends("session_line_ids")
    def compute_agent_count(self):
        for record in self:
            record.agent_count = len(set(record.session_line_ids.mapped('ai_agent_id')))

    @api.onchange('model_id')
    def _onchange_model_id(self):
        if self.init_type == 'server-action':
            if self.server_action_id:
                self.server_action_id.write({
                    'name': self.name,
                    'model_id': self.model_id.id,
                    'binding_model_id': self.model_id.id,
                })
        if self.init_type == 'cron':
            if self.cron_id:
                self.cron_id.write({
                    'name': self.name,
                    'model_id': self.model_id.id,
                })

    @api.onchange('init_type')
    def _onchange_init_type(self):
        name = self.name
        # ~ if self.init_type != 'cron' and self.cron_id:
        # ~ self.cron_id.unlink()
        if self.init_type == 'cron':
            if not self.cron_id:
                self.cron_id = self.cron_id.create({
                    'name': self.name,
                    'model_id': self.model_id.id if self.model_id else self.env.ref('base.model_res_partner').id,
                    'state': 'code',
                    'code': f"action = env.ref('{self._get_eid()}').cron()",
                })
        if self.init_type != 'server-action' and self.server_action_id:
            self.server_action_id.unlink()

        if self.init_type == 'server-action':
            if not self.server_action_id:
                self.server_action_id = self.server_action_id.create({
                    'name': self.name,
                    'model_id': self.model_id.id if self.model_id else self.env.ref('base.model_res_partner').id,
                    'state': 'code',
                    'code': f"action = env.ref('{self._get_eid()}').server_action(records)",
                })
        if self.init_type != 'channel' and self.channel_id:
            self.channel_id.unlink()

        if self.init_type == 'channel':
            if not self.channel_id:
                self.channel_id = self.channel_id.create({
                    'name': self.name,
                    'ai_quest_id': self.id,
                })
        if self.init_type != 'chat' and self.chat_user_id:
            self.chat_user_id.unlink()

        if self.init_type == 'chat':
            if not self.chat_user_id:
                self.chat_user_id = self.chat_user_id.create({
                    'name': self.name,
                    'login': self.name,
                })
        self.name = name

    def _get_eid(self):
        if not self.name:
            raise ValidationError("Set a name for this quest")
        eid = list(self.get_external_id().values())[0]
        if not eid:
            self.env['ir.model.data'].create({
                'name': unidecode.unidecode(
                    re.sub(
                        r'[^a-zA-Z0-9åäö\s]', '', self.name.lower()
                    ).replace(' ', '_')) + f"_{int(''.join(filter(str.isdigit, str(self.id))))}",
                'module': 'new',
                'model': 'ai.quest',
                'res_id': self.id,
            })
        return eid

    def log_message(self, body, is_error=False):
        if is_error:
            self.status = "error"
        self.last_run = fields.Datetime.now()
        self.message_post(body=f"{body} | {self.last_run}", message_type="notification")

    def mail(self, mail, session):
        # _logger.error(f"{session.session=}")
        if self.init_type == "mail":
            # parser = JsonOutputParser(pydantic_object=jsonResponse)
            agent_id = self.ai_agent_ids[0].ai_agent_id
            # response = agent_id.prompt_agent(mail=mail, session=session)
            response = self.run(mail=mail)
            response = response.replace('json\n', '').replace('```', '')
            response = json.loads(response)
            session.message_post(body=f"{response}", message_type="notification")

    def mail_test_wizard(self):
        action = self.env.ref("ai_agent.action_ai_quest_test_mail_wizard").read()[0]
        # _logger.error(f"{action=}")
        action["context"] = {"default_ai_quest_id": self.id}
        return action

    # ------------------------------------------------------------
    # Init type API
    # ------------------------------------------------------------

    def _server_action_values(self, **kwarg):
        return {
            'agent': self.ai_agent_ids[0].ai_agent_id,
            'session': self.env['ai.quest.session'].quest_init(self, agents=[self.ai_agent_ids[0].ai_agent_id]),
        }

    def server_action(self, records):
        if self.init_type == 'server-action' and self.server_action_id:
            vals = self._server_action_values(records=records)

            for rec in records:
                res = self.with_context({'records': records, 'session': vals['session']}).run()
                rec.write({'comment': markdown.markdown(res)})
            self.log_message(f'server-action {records}')

            #     vals = self._server_action_values(records=records)
            #     if self.code:
            #         return self.with_context({'records': records, 'session': vals['session']}).run()
            #     else:
            #         return vals['agent'].prompt_agent('',session=vals['session'])
            #

    def _cron_values(self, **kwarg):
        return {
            'agent': self.ai_agent_ids[0].ai_agent_id,
            'session': self.env['ai.quest.session'].quest_init(self, agents=[self.ai_agent_ids[0].ai_agent_id]),
        }

    def cron(self, records):
        self.ensure_one()
        if self.init_type == 'cron' and self.cron_id:
            vals = self._cron_values()
            if self.code:
                return self.with_context({'records': records, 'session': vals['session']}).run()
            else:
                return vals['agent'].prompt_agent('', session=vals['session'])

    def _chat_values(self, **kwarg):
        return {'agent': self.ai_agent_ids[0].ai_agent_id,
                'session': kwarg['message'].parent_id.ai_quest_session_id if kwarg['message'].parent_id and kwarg[
                    'message'].parent_id.ai_quest_session_id else \
                    kwarg['message'].parent_id.ai_quest_session_id if kwarg['message'].ai_quest_session_id else \
                        self.env['ai.quest.session'].quest_init(self, agents=[self.ai_agent_ids[0].ai_agent_id]),
                }

    def chat(self, message):
        if self.init_type == 'chat' or "channel" and self.channel_id:
            vals = self._chat_values(message=message)
            if self.code:
                return self.with_context({'parameter': message, 'session': vals['session']}).run()
            else:
                return vals['agent'].prompt_agent(message.body, session=vals['session'])

    # ------------------------------------------------------------
    # Python CODE eval
    # ------------------------------------------------------------

    def _get_eval_context(self, action=None, kw=None):
        """ Prepare the context used when evaluating python code, like the
        python formulas or code server actions.

        :param action: the current server action
        :type action: browse record
        :returns: dict -- evaluation context given to (safe_)safe_eval """

        agent = self.ai_agent_ids[0].ai_agent_id
        prompt_template = PromptTemplate(
            template=agent.ai_prompt_template, input_variables=["context", "document", "question"]
        )

        eval_context = {}
        model_name = self._name
        model = self.env[model_name]
        record = None
        records = None
        if self._context.get('active_model') == model_name and self._context.get('active_id'):
            record = model.browse(self._context['active_id'])
        if self._context.get('active_model') == model_name and self._context.get('active_ids'):
            records = model.browse(self._context['active_ids'])
        if self._context.get('onchange_self'):
            record = self._context['onchange_self']
        eval_context.update({
            # orm
            'env': self.env,
            'model': model,
            # Exceptions
            'Warning': Warning,
            'UserError': UserError,
            # record
            'record': record,
            'records': records,
            # helpers
            # 'log': log,
            'session': self.env['ai.quest.session'].quest_init(self, agents=[agent]),
            'markdown': markdown.markdown,
            'self': self,
            'agent': agent,
            'prompt_template': prompt_template,
            'company_id': self.env.user.company_id,
            'context': self.env.context,

            **kw,

            # langgraph
            'START': START,
            'END': END,
            'ToolNode': ToolNode,
            'StateGraph': StateGraph,
            'MessagesState': MessagesState,
            'HumanMessage': HumanMessage,
            'ChatOpenAI': ChatOpenAI,

            'MemorySaver': MemorySaver,
            # 'DuckDuckGoSearchRun': DuckDuckGoSearchRun()
        })
        return eval_context

    def _get_runner(self):
        multi = True
        t = self.env.registry[self._name]
        return getattr(t, '_run_action_code_multi'), multi

    def _run_action_code_multi(self, eval_context):
        safe_eval(self.code.strip(), eval_context, mode="exec", nocopy=True, filename=str(self))
        return eval_context.get('action')

    def _get_agent_session(self, agent):
        return self.env['ai.quest.session'].quest_init(self, agents=[agent])

    def run(self, **kwargs):
        res = False
        for action in self.sudo():
            eval_context = self._get_eval_context(action, kwargs)
            records = self.env.context.get('records')
            if records:
                try:
                    records.check_access_rule('write')
                except AccessError:
                    _logger.warning(
                        "Forbidden server action %r executed while the user %s does not have access to %s.",
                        action.name, self.env.user.login, records,
                    )
                    raise

            runner, multi = action._get_runner()

            agent = self.ai_agent_ids[0].ai_agent_id
            session = self._get_agent_session(agent)

            if runner and multi:
                # call the multi method
                run_self = action.with_context(eval_context['env'].context)
                res = runner(run_self, eval_context=eval_context)

                return res
            else:
                _logger.warning(
                    "Found no way to execute server action %r of type %r, ignoring it. "
                    "Verify that the type is correct or add a method called "
                    "`_run_action_<type>` or `_run_action_<type>_multi`.",
                    action.name, action.state
                )
        return res or False

    # ------------------------------------------------------------
    # ORM
    # ------------------------------------------------------------

    def _alias_get_creation_values(self):
        values = super(AIQuest, self)._alias_get_creation_values()
        values['alias_model_id'] = self.env['ir.model']._get('ai.quest.session').id
        if self.id:
            values['alias_defaults'] = defaults = {}
            defaults['ai_quest_id'] = self.id
        return values

    def write(self, vals):
        result = super(AIQuest, self).write(vals)
        if 'init_type' in vals and vals.get('init_type') == 'mail':
            for quest in self:
                alias_vals = quest._alias_get_creation_values()
                quest.write({
                    'alias_name': alias_vals.get('alias_name', quest.alias_name),
                    'alias_defaults': alias_vals.get('alias_defaults'),
                })
        for quest in self:
            if quest.server_action_id:
                quest.server_action_id.write(
                    {'name': quest.name, 'code': f"action = env.ref('{quest._get_eid()}').server_action(records)"})
            if quest.cron_id:
                quest.cron_id.write({'name': quest.name, 'code': f"action = env.ref('{quest._get_eid()}').cron()"})
            if quest.channel_id:
                quest.channel_id.write({'name': quest.name, 'ai_quest_id': quest.id, })
            if quest.chat_user_id:
                quest.chat_user_id.write({'name': quest.name, 'login': quest.name, 'ai_quest_id': quest.id, })
        return result


class MailMessage(models.Model):
    _inherit = 'mail.message'

    ai_quest_session_id = fields.Many2one(comodel_name='ai.quest.session', string="Session", help="")


class MailChannel(models.Model):
    _inherit = 'mail.channel'

    ai_quest_id = fields.Many2one(comodel_name='ai.quest', string="Quest", help="")
    ai_quest_session_id = fields.Many2one(comodel_name='ai.quest.session', string="Session", help="")

    @api.returns('mail.message', lambda value: value.id)
    def message_post(self, **kwargs):
        message = super(MailChannel, self).message_post(**kwargs)

        # Check if the message is from a user (not the bot itself)
        _logger.warning(f"{message.author_id=} {message.parent_id=} {self.ai_quest_id=}")
        if message.author_id != self.env.ref('base.partner_root'):
            if self.ai_quest_id:
                bot_response = self.ai_quest_id.chat(message)
                _logger.error(f"{bot_response=}")
                if bot_response:
                    self.with_user(self.env.ref('base.user_root')).message_post(
                        body=bot_response,
                        message_type='comment',
                        subtype_xmlid='mail.mt_comment',
                    )
        return message


class ResUsers(models.Model):
    _inherit = 'res.users'

    ai_quest_id = fields.Many2one(comodel_name='ai.quest', string="Quest", help="")
    ai_quest_session_id = fields.Many2one(comodel_name='ai.quest.session', string="Session", help="")
