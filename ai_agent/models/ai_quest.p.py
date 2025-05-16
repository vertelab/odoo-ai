import base64
import ast
import json
import logging
import markdown
import time
import operator
import re
import traceback
import unidecode
import warnings
import math

from datetime import date, timedelta
from IPython.display import Image, display
from langchain import hub
from langchain.agents import Tool, AgentExecutor, LLMSingleActionAgent, AgentOutputParser, create_tool_calling_agent, \
    create_xml_agent, create_json_chat_agent
from langchain.prompts import ChatPromptTemplate
from langchain.schema import AgentAction, AgentFinish
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.runnables.graph import MermaidDrawMethod
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import StateGraph, START, END
from langgraph.prebuilt import create_react_agent
from odoo import models, fields, api, _
from odoo.addons.ai_agent.models.ai_quest_session import AIQuestSession
from odoo.exceptions import UserError, ValidationError
# #if VERSION <= "16.0"
from odoo.exceptions import Warning
# #endif
from odoo.tools.mail import html2plaintext
from odoo.tools.safe_eval import safe_eval
from pydantic import BaseModel, ConfigDict, SkipValidation
from random import randint
from secrets import choice
from .utils import graph_to_mermaid

##if VERSION >= '18.0'
from typing import Optional, Annotated, List, NotRequired, Sequence, TypedDict, Union, Any
##else
from typing_extensions import NotRequired, TypedDict
from typing import Optional, Annotated, List, Dict, Sequence, Union, Any
##endif
# Odoo 18 okt 2024  Ubuntu 24.04 Python 3.12 (NotRequired 3.11)
# Odoo 17 2023 Ubuntu 22.04  Python 3.10
# Odoo 16 2022 Ubuntu 22.04  Python 3.10
# Odoo 14 2020 Ubuntu 20.04  Python 3.8 -> 3.10
# The typing_extensions module is primarily used for backporting new features to older Python versions


##if VERSION >= '16.0'
from odoo.addons.base.models.avatar_mixin import get_hsl_from_seed

##endif

_logger = logging.getLogger(__name__)

SUPERVISOR = """You are a supervisor coordinating between workers: {members}.
Based on the request, determine which worker should handle the next step.
Only choose FINISH when a complete response has been provided.

Guidelines: {self.description}
{self._extra_context(self)}

Instructions:
1. Choose the most appropriate worker first
2. Evaluate if we have a complete response
3. Send FINISH only when we have a satisfactory response
4. Do not mention that you have done tool calls, that's too technical 
5. {use_lang}
"""

avatar_channel = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 530.06 530.06">
<circle cx="265.03" cy="265.03" r="265.03" fill="#875a7b"/>
<path d="M416.74,217.29l5-28a8.4,8.4,0,0,0-8.27-9.88H361.09l10.24-57.34a8.4,8.4,0,0,0-8.27-9.88H334.61a8.4,8.4,0,0,0-8.27,6.93L315.57,179.4H246.5l10.24-57.34a8.4,8.4,0,0,0-8.27-9.88H220a8.4,8.4,0,0,0-8.27,6.93L201,179.4H145.6a8.42,8.42,0,0,0-8.28,6.93l-5,28a8.4,8.4,0,0,0,8.27,9.88H193l-16,89.62H121.59a8.4,8.4,0,0,0-8.27,6.93l-5,28a8.4,8.4,0,0,0,8.27,9.88H169L158.73,416a8.4,8.4,0,0,0,8.27,9.88h28.45a8.42,8.42,0,0,0,8.28-6.93l10.76-60.29h69.07L273.32,416a8.4,8.4,0,0,0,8.27,9.88H310a8.4,8.4,0,0,0,8.27-6.93l10.77-60.29h55.38a8.41,8.41,0,0,0,8.28-6.93l5-28a8.4,8.4,0,0,0-8.27-9.88H337.08l16-89.62h55.38A8.4,8.4,0,0,0,416.74,217.29ZM291.56,313.84H222.5l16-89.62h69.07Z" fill="#ffffff"/>
</svg>'''
avatar_group = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 530.06 530.06">
<circle cx="265.03" cy="265.03" r="265.03" fill="#875a7b"/>
<path d="m184.356059,265.030004c-23.740561,0.73266 -43.157922,10.11172 -58.252302,28.136961l-29.455881,0c-12.0169,0 -22.128621,-2.96757 -30.335161,-8.90271s-12.309921,-14.618031 -12.309921,-26.048671c0,-51.730902 9.08582,-77.596463 27.257681,-77.596463c0.87928,0 4.06667,1.53874 9.56217,4.61622s12.639651,6.19167 21.432451,9.34235s17.512401,4.72613 26.158581,4.72613c9.8187,0 19.563981,-1.68536 29.236061,-5.05586c-0.73266,5.4223 -1.0991,10.25834 -1.0991,14.508121c0,20.370061 5.93514,39.127962 17.805421,56.273922zm235.42723,140.025346c0,17.585601 -5.34888,31.470971 -16.046861,41.655892s-24.912861,15.277491 -42.645082,15.277491l-192.122688,0c-17.732221,0 -31.947101,-5.09257 -42.645082,-15.277491s-16.046861,-24.070291 -16.046861,-41.655892c0,-7.7669 0.25653,-15.350691 0.76937,-22.751371s1.53874,-15.387401 3.07748,-23.960381s3.48041,-16.523211 5.82523,-23.850471s5.4955,-14.471411 9.45226,-21.432451s8.49978,-12.89618 13.628841,-17.805421c5.12906,-4.90924 11.393931,-8.82951 18.794611,-11.76037s15.570511,-4.3964 24.509931,-4.3964c1.46554,0 4.61622,1.57545 9.45226,4.72613s10.18492,6.6678 16.046861,10.55136c5.86194,3.88356 13.702041,7.40068 23.520741,10.55136s19.710601,4.72613 29.675701,4.72613s19.857001,-1.57545 29.675701,-4.72613s17.658801,-6.6678 23.520741,-10.55136c5.86194,-3.88356 11.21082,-7.40068 16.046861,-10.55136s7.98672,-4.72613 9.45226,-4.72613c8.93942,0 17.109251,1.46554 24.509931,4.3964s13.665551,6.85113 18.794611,11.76037c5.12906,4.90924 9.67208,10.844381 13.628841,17.805421s7.10744,14.105191 9.45226,21.432451s4.28649,15.277491 5.82523,23.850471s2.56464,16.559701 3.07748,23.960381s0.76937,14.984471 0.76937,22.751371zm-225.095689,-280.710152c0,15.534021 -5.4955,28.796421 -16.486501,39.787422s-24.253401,16.486501 -39.787422,16.486501s-28.796421,-5.4955 -39.787422,-16.486501s-16.486501,-24.253401 -16.486501,-39.787422s5.4955,-28.796421 16.486501,-39.787422s24.253401,-16.486501 39.787422,-16.486501s28.796421,5.4955 39.787422,16.486501s16.486501,24.253401 16.486501,39.787422zm154.753287,84.410884c0,23.300921 -8.24325,43.194632 -24.729751,59.681133s-36.380212,24.729751 -59.681133,24.729751s-43.194632,-8.24325 -59.681133,-24.729751s-24.729751,-36.380212 -24.729751,-59.681133s8.24325,-43.194632 24.729751,-59.681133s36.380212,-24.729751 59.681133,-24.729751s43.194632,8.24325 59.681133,24.729751s24.729751,36.380212 24.729751,59.681133zm126.616325,49.459502c0,11.43064 -4.10338,20.113531 -12.309921,26.048671s-18.318261,8.90271 -30.335161,8.90271l-29.455881,0c-15.094381,-18.025241 -34.511741,-27.404301 -58.252302,-28.136961c11.87028,-17.145961 17.805421,-35.903862 17.805421,-56.273922c0,-4.24978 -0.36644,-9.08582 -1.0991,-14.508121c9.67208,3.3705 19.417361,5.05586 29.236061,5.05586c8.64618,0 17.365781,-1.57545 26.158581,-4.72613s15.936951,-6.26487 21.432451,-9.34235s8.68289,-4.61622 9.56217,-4.61622c18.171861,0 27.257681,25.865561 27.257681,77.596463zm-28.136961,-133.870386c0,15.534021 -5.4955,28.796421 -16.486501,39.787422s-24.253401,16.486501 -39.787422,16.486501s-28.796421,-5.4955 -39.787422,-16.486501s-16.486501,-24.253401 -16.486501,-39.787422s5.4955,-28.796421 16.486501,-39.787422s24.253401,-16.486501 39.787422,-16.486501s28.796421,5.4955 39.787422,16.486501s16.486501,24.253401 16.486501,39.787422z" fill="#ffffff"/>
</svg>'''
avatar_mail = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 530.06 530.06">
<circle cx="265.03" cy="265.03" r="265.03" fill="#875a7b"/>
<path d="M424.05 159.02H106.01c-11.05 0-20 8.95-20 20v172.02c0 11.05 8.95 20 20 20h318.04c11.05 0 20-8.95 20-20V179.02c0-11.05-8.95-20-20-20zm-20 33.46v8.56L265.03 282.7 126.01 201.04v-8.56h278.04zM126.01 331.04V234.5l124.43 73.19c8.95 5.27 20.2 5.27 29.15 0L404.05 234.5v96.54H126.01z" fill="#ffffff"/>
<path d="M265.03 318.04c-29.15 0-52.81 23.66-52.81 52.81s23.66 52.81 52.81 52.81 52.81-23.66 52.81-52.81-23.66-52.81-52.81-52.81zm0 79.22c-14.58 0-26.41-11.83-26.41-26.41s11.83-26.41 26.41-26.41 26.41 11.83 26.41 26.41-11.83 26.41-26.41 26.41z" fill="#ffffff"/>
<circle cx="265.03" cy="370.85" r="13.2" fill="#ffffff"/>
</svg>'''
avatar_cron = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 530.06 530.06">
<circle cx="265.03" cy="265.03" r="265.03" fill="#875a7b"/>
<path d="M265.03 106.01c-87.83 0-159.02 71.19-159.02 159.02s71.19 159.02 159.02 159.02 159.02-71.19 159.02-159.02S352.86 106.01 265.03 106.01zm0 291.54c-73.19 0-132.52-59.33-132.52-132.52s59.33-132.52 132.52-132.52 132.52 59.33 132.52 132.52-59.33 132.52-132.52 132.52z" fill="#ffffff"/>
<path d="M265.03 172.52c-51.23 0-92.51 41.28-92.51 92.51s41.28 92.51 92.51 92.51 92.51-41.28 92.51-92.51-41.28-92.51-92.51-92.51zm0 158.52c-36.59 0-66.01-29.42-66.01-66.01s29.42-66.01 66.01-66.01 66.01 29.42 66.01 66.01-29.42 66.01-66.01 66.01z" fill="#ffffff"/>
<path d="M265.03 225.53c-21.79 0-39.5 17.71-39.5 39.5s17.71 39.5 39.5 39.5 39.5-17.71 39.5-39.5-17.71-39.5-39.5-39.5z" fill="#ffffff"/>
</svg>'''
avatar_manual = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 530.06 530.06">
<circle cx="265.03" cy="265.03" r="265.03" fill="#875a7b"/>
<path d="M371.04 265.03L212.02 159.02v212.02l159.02-106.01zm-132.52 79.51V185.52l106.01 79.51-106.01 79.51z" fill="#ffffff"/>
<path d="M265.03 106.01c-87.83 0-159.02 71.19-159.02 159.02s71.19 159.02 159.02 159.02 159.02-71.19 159.02-159.02S352.86 106.01 265.03 106.01zm0 291.54c-73.19 0-132.52-59.33-132.52-132.52s59.33-132.52 132.52-132.52 132.52 59.33 132.52 132.52-59.33 132.52-132.52 132.52z" fill="#ffffff"/>
</svg>'''
avatar_chat = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 530.06 530.06">
<circle cx="265.03" cy="265.03" r="265.03" fill="#875a7b"/>
<path d="M371.04 212.02H159.02c-14.58 0-26.41 11.83-26.41 26.41v132.52c0 14.58 11.83 26.41 26.41 26.41h212.02c14.58 0 26.41-11.83 26.41-26.41V238.43c0-14.58-11.83-26.41-26.41-26.41zm0 158.93H159.02V238.43h212.02v132.52z" fill="#ffffff"/>
<circle cx="212.02" cy="291.44" r="26.41" fill="#ffffff"/>
<circle cx="318.04" cy="291.44" r="26.41" fill="#ffffff"/>
<path d="M265.03 132.52c-29.15 0-52.81 23.66-52.81 52.81v26.41h105.62v-26.41c0-29.15-23.66-52.81-52.81-52.81zm0 52.81c-14.58 0-26.41-11.83-26.41-26.41s11.83-26.41 26.41-26.41 26.41 11.83 26.41 26.41-11.83 26.41-26.41 26.41z" fill="#ffffff"/>
<rect x="238.62" y="344.25" width="52.81" height="26.41" fill="#ffffff"/>
</svg>'''
avatar_server_action = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 530.06 530.06">
<circle cx="265.03" cy="265.03" r="265.03" fill="#875a7b"/>
<path d="M371.04 159.02H159.02c-14.58 0-26.41 11.83-26.41 26.41v159.02c0 14.58 11.83 26.41 26.41 26.41h212.02c14.58 0 26.41-11.83 26.41-26.41V185.43c0-14.58-11.83-26.41-26.41-26.41zm0 185.43H159.02V185.43h212.02v159.02z" fill="#ffffff"/>
<path d="M212.02 238.43h105.62v26.41H212.02zM212.02 291.44h105.62v26.41H212.02z" fill="#ffffff"/>
<circle cx="345.04" cy="265.03" r="26.41" fill="#ffffff"/>
</svg>'''
powerbox = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 530.06 530.06">
<circle cx="265.03" cy="265.03" r="265.03" fill="#875a7b"/>
<path d="M371.04 159.02H159.02c-14.58 0-26.41 11.83-26.41 26.41v159.02c0 14.58 11.83 26.41 26.41 26.41h212.02c14.58 0 26.41-11.83 26.41-26.41V185.43c0-14.58-11.83-26.41-26.41-26.41zm0 185.43H159.02V185.43h212.02v159.02z" fill="#ffffff"/>
<path d="M212.02 238.43h105.62v26.41H212.02zM212.02 291.44h105.62v26.41H212.02z" fill="#ffffff"/>
<circle cx="345.04" cy="265.03" r="26.41" fill="#ffffff"/>
</svg>'''


class AIQuestAgent(models.Model):
    _name = 'ai.quest.agent'
    _description = 'AI Quest Agent'
    _order = "sequence asc"

    ai_quest_id = fields.Many2one(comodel_name='ai.quest', string="", help="")
    ai_agent_id = fields.Many2one(
        comodel_name='ai.agent', string="Agent", help="", required=False)
    ai_agent_status = fields.Selection(selection=[
        ("draft", "Draft"), ("active", "Active"), ("done", "Done"), ("error", "Error")], default="draft", related='ai_agent_id.status')
    ai_agent_llm_id = fields.Many2one(comodel_name="ai.agent.llm", string="LLM", help="Choose Large Language Model",
                                      domain="[('status','=','confirmed')]", related='ai_agent_id.ai_agent_llm_id')
    ai_llm_status = fields.Selection(
        selection=[("not_confirmed", "Not Confirmed"), ("confirmed", "Confirmed"), ("error", "Error")],
        default="not_confirmed", related='ai_agent_id.ai_agent_llm_id.status')
    object_id = fields.Reference(string='Object', related="ai_agent_id.object_id")
    sequence = fields.Integer(string='Sequence')


# https://readmedium.com/langgraph-made-easy-a-beginners-guide-part-2-196e8b179119

DEFAULT_PYTHON_CODE = """# Available variables:  
#  - env, self: Odoo Environment on which the action is triggered
#  - quest: The current Quest
#  - agents: A list of agents for this Quest
#  - company_id: The current Company
#  - context: The current context
#  - record: record on which the action is triggered; may be void
#  - records: recordset of all records on which the action is triggered in multi-mode; may be void
#  - message: Human message record
#  - message_body: Human message in a chat context
#  - message_invoke: Invoke-parameter in a chat context
#  - UserError: Raise UserError-condition 
#  - _logger: Logger eg _logger.warning(f"My text")
# To return a result assign result\n
# Example:
#
# result = quest.build(session=session,message=message_body).invoke(message_invoke)
#\n\n\n
"""

# Python code
INIT_TYPES = [
    ('manual', 'Manual'),
    ('mail', 'Mail'),
    ('chat', 'Chat with User'),
    ('channel', 'Chat with Channel'),
    ('cron', 'Scheduled Action'),
    ('server-action', 'Server Action'),
    ('powerbox', 'Powerbox'),
]


class AIQuest(models.Model):
    _name = 'ai.quest'
    _inherit = ["mail.thread", "mail.activity.mixin", "mail.alias.mixin"]
    _description = 'AI Quest'

    agent_count = fields.Integer(compute="compute_agent_count")
    ai_agent_ids = fields.One2many(comodel_name='ai.quest.agent', inverse_name='ai_quest_id')
    ai_type = fields.Selection(selection=[("default", "Default"), ('ai-programmer', 'AI Programmer')],
                               default="default", required=True)
    alias_id = fields.Many2one(comodel_name='mail.alias', string='Alias', ondelete="restrict", required=False,
                               help="The email address associated with this channel. New emails received will "
                                    "automatically create new leads assigned to the channel.")
    # #if VERSION <= "17.0"
    alias_user_id = fields.Many2one(comodel_name='res.users', related='alias_id.alias_user_id', readonly=False,
                                    inherited=True)
    # #endif

    # #if VERSION == "14.0"
    avatar_128 = fields.Image("Avatar", max_width=128, max_height=128)
    # #elif VERSION >= "15.0"
    avatar_128 = fields.Image("Avatar", max_width=128, max_height=128, compute='_compute_avatar_128')
    # #endif

    # #if VERSION >= "17.0"
    channel_id = fields.Many2one(comodel_name='discuss.channel', string="Channel", help="")
    real_channel_id = fields.Many2one(comodel_name='discuss.channel', string="Channel",
                                      help="This is the channel chat-method get")
    # #elif VERSION <= "16.0"
    channel_id = fields.Many2one(comodel_name='mail.channel', string="Channel", help="")
    real_channel_id = fields.Many2one(comodel_name='mail.channel', string="Channel",
                                      help="This is the channel chat-method get")
    # #endif

    chat_history_limit = fields.Integer(string='Chat History Limit', default=10,
                                        help='Limit the chat history to this number of messages')
    chat_user_id = fields.Many2one(comodel_name='res.users', string="Chat User", help="", readonly=True)
    code = fields.Text(string='Python Code', default=DEFAULT_PYTHON_CODE,
                       help="Write Python code that the action will execute. Some variables are available for use; "
                            "help about python expression is given in the help tab.")
    color = fields.Integer(default=lambda self: randint(1, 11))
    cron_id = fields.Many2one(comodel_name='ir.cron', string="Scheduled Action", help="", ondelete="cascade")
    debug = fields.Boolean(string='Debug', help='More logging')
    description = fields.Text()
    filter_domain = fields.Char(string='Record Selection', )
    image_128 = fields.Image("Image", max_width=128, max_height=128)
    init_type = fields.Selection(selection=INIT_TYPES, string='Initiate', help="How the Quest is initialized",
                                 required=True, default='manual')
    init_type_str = fields.Html(string='', )
    is_favorite = fields.Boolean()
    last_run = fields.Datetime()
    llm_count = fields.Integer(compute="compute_llm_count")
    model_id = fields.Many2one(comodel_name='ir.model', string="Model", help="Bind this Quest to this model")
    model_name = fields.Char(related='model_id.model', string='Model Name', readonly=True, store=True)
    name = fields.Char(required=True)
    partner_id = fields.Many2one(comodel_name='res.partner', string="Customer", help="")
    real_chat_user_id = fields.Many2one(comodel_name='res.users', string="Chat User",
                                        help="Chat user thet chat-method is using")
    server_action_id = fields.Many2one('ir.actions.server', string='Server Action',
                                       help="Server action to be executed when this quest is initialized",
                                       ondelete="cascade")

    session_count = fields.Integer(compute="compute_session_count")
    session_ids = fields.One2many(comodel_name="ai.quest.session", inverse_name="ai_quest_id")
    session_line_count = fields.Integer(compute="compute_session_line_count")
    session_line_ids = fields.One2many(comodel_name="ai.quest.session.line", inverse_name="ai_quest_id")
    session_object_count = fields.Integer(compute="compute_session_object_count")
    session_object_ids = fields.One2many(comodel_name="ai.session.object", inverse_name="ai_quest_id")
    sub_description = fields.Char(string="Sub Description")
    status = fields.Selection(
        selection=[("draft", "Draft"), ("active", "Active"), ("done", "Done"), ("error", "Error")],
        default="draft")
    # #if VERSION >= '16.0' 
    tag_ids = fields.Many2many(comodel_name='product.tag', string='Tags')
    # #endif
    use_chat_history = fields.Boolean(string='Use Chat History', default=True, help='Add chat history to the context')
    use_company_info = fields.Boolean(string='Use Company Info', default=True,
                                      help='Add company mission and values to the context')
    use_personal_info = fields.Boolean(string='Use Personal Info', default=True,
                                       help='Add personal name and other info to the context')
    use_personal_lang = fields.Boolean(string='Use the Users Language', default=True,
                                       help='Set Personas language for the LLM')
    use_time_context = fields.Boolean(string='Use Time Context', default=True,
                                      help='Inform the LLM of current time, date')
    user_id = fields.Many2one(comodel_name='res.users', string="Owner", help="")
    user_in_group = fields.Boolean(compute='_compute_user_in_group')
    is_supervisor = fields.Boolean(string='Is Supervisor',
                                   help="This is a ReAct type of quest using a supervisor coordinating agents")
    supervisor_prompt = fields.Text(string="Supervisor Prompt", default=SUPERVISOR)
    supervisor_llm_id = fields.Many2one(comodel_name="ai.agent.llm", string="LLM",
                                        help="Choose Large Language Model for the supervisor",
                                        domain="[('status','=','confirmed')]")
    supervisor_temperature = fields.Float(
        string='Temperature', default=0.7,
        help="Temperature controls the randomness and creativity of the model's output, "
             "<1.0 more predictable and consistent >1.0 more diverse and creative responses"
    )
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company)
    company_partner_id = fields.Many2one('res.partner', related="company_id.partner_id")
    allow_trigger_words = fields.Boolean(string="Use activation word")
    chat_trigger_words = fields.Text(string="Activation word", help="Separate words using commas")
    supervisor_cycles = fields.Integer(string="Supervisor Cycles", default=5, help="This field dictates how many cycles a supervisor can go through before terminating and returning an answer.")

    @api.model
    def get_xmlrpc_quests(self):
        quests = 0
        quest_ids = self.search([])
        quests += len(quest_ids)
        for quest_id in quest_ids:
            if add := math.floor(quest_id.session_line_count / 1000000):
                quests += add
        _logger.error(f"{quests=}")
        return quests

    # #if VERSION >= '16.0'
    @api.depends('is_supervisor',
                 'ai_agent_ids.sequence',
                 'ai_agent_ids.ai_agent_id',
                 'ai_agent_ids.ai_agent_id.ai_agent_llm_id',
                 'ai_agent_ids.ai_agent_id.ai_tool_ids.ai_tool_id',
                 'ai_agent_ids.ai_agent_id.ai_memory_ids.ai_memory_id')
    def _compute_graph_image(self):
        for rec in self:
            real_ai_agent_ids = list(set(filter(lambda ai_agent_id: ai_agent_id.ai_agent_id.id, rec.ai_agent_ids)))
            if real_ai_agent_ids:
                try:
                    graph = rec.build(session=self.env['ai.quest.session'].quest_init(rec), mermaid=True)
                    rec.mermaid_graph = graph_to_mermaid(graph.get_graph())
                except Exception as e:
                    raise UserError(f"Error building chain: {str(e)}\n{traceback.format_exc()}")
            else:
                rec.mermaid_graph = False

    mermaid_graph = fields.Text("Graph Text", compute=_compute_graph_image, compute_sudo=True, store=False)
    ## endif

    @api.model
    def _generate_random_token(self):
        return ''.join(choice('abcdefghijkmnopqrstuvwxyzABCDEFGHIJKLMNPQRSTUVWXYZ23456789') for _i in range(10))

    uuid = fields.Char('UUID', size=50, default=_generate_random_token, copy=False)

    # #if VERSION >= '16.0'
    @api.depends('init_type', 'image_128', 'uuid')
    def _compute_avatar_128(self):
        for record in self:
            record.avatar_128 = record.image_128 or record._generate_avatar()

    def _generate_avatar(self):
        avatar = {
            'manual': avatar_manual,
            'mail': avatar_mail,
            'chat': avatar_chat,
            'channel': avatar_channel,
            'cron': avatar_cron,
            'server-action': avatar_cron,
            'powerbox': powerbox,
        }[self.init_type]
        bgcolor = get_hsl_from_seed(self.uuid)
        avatar = avatar.replace('fill="#875a7b"', f'fill="{bgcolor}"')
        return base64.b64encode(avatar.encode())

    # #endif

    def _compute_user_in_group(self):
        for record in self:
            record.user_in_group = self.env.user.has_group('ai_agent.group_ai_agent_manager')

    @api.depends('session_line_ids')
    def compute_llm_count(self):
        for record in self:
            record.llm_count = len(set(record.session_line_ids.mapped('ai_llm_id')))

    def action_get_llms(self):
        llm_ids = []
        [llm_ids.extend(session_id.ai_agent_llm_ids.ids) for session_id in self.session_ids]
        action = {
            'name': 'LLMs',
            'type': 'ir.actions.act_window',
            'res_model': 'ai.agent.llm',
            # #if VERSION >= "18.0"
            'view_mode': 'kanban,list,form,calendar',
            # #elif VERSION <= "17.0"
            'view_mode': 'kanban,tree,form,calendar',
            # #endif
            'target': 'current',
            'domain': [("id", '=', llm_ids)]
        }
        return action

    def action_get_session_lines(self):
        action = {
            'name': 'Session Lines',
            'type': 'ir.actions.act_window',
            'res_model': 'ai.quest.session.line',
            # #if VERSION >= "18.0"
            'view_mode': 'list,form',
            # #elif VERSION <= "17.0"
            'view_mode': 'tree,form',
            # #endif
            'target': 'current',
            'domain': [("ai_quest_id", '=', self.id)]
        }
        return action

    def action_get_sessions(self):
        action = {
            'name': 'Sessions',
            'type': 'ir.actions.act_window',
            'res_model': 'ai.quest.session',
            # #if VERSION >= "18.0"
            'view_mode': 'list,form',
            # #elif VERSION <= "17.0"
            'view_mode': 'tree,form',
            # #endif
            'target': 'current',
            'domain': [("ai_quest_id", '=', self.id)]
        }
        return action

    def action_get_agents(self):
        ai_agent_ids = list(map(lambda session_id: session_id.ai_agent_ids.ids, self.session_ids))
        agent_ids = []
        [agent_ids.extend(ai_agent_id) for ai_agent_id in ai_agent_ids]
        agent_ids = list(set(agent_ids))
        action = {
            'name': 'AI Agents',
            'type': 'ir.actions.act_window',
            'res_model': 'ai.agent',
            # #if VERSION >= "18.0"
            'view_mode': 'kanban,list,form',
            # #elif VERSION <= "17.0"
            'view_mode': 'kanban,tree,form',
            # #endif
            'target': 'current',
            'domain': [("id", 'in', agent_ids)]
        }
        return action

    def action_get_session_objects(self):
        action = {
            'name': 'Objetcs',
            'type': 'ir.actions.act_window',
            'res_model': 'ai.session.object',
            # #if VERSION >= "18.0"
            'view_mode': 'list,calendar',
            # #elif VERSION <= "17.0"
            'view_mode': 'tree,calendar',
            # #endif
            'target': 'current',
            'context': {
                "expand": 1
            },
            'domain': [("ai_quest_id", '=', self.id)]
        }
        return action

    @api.depends("session_object_ids")
    def compute_session_object_count(self):
        for record in self:
            record.session_object_count = len(record.session_object_ids)

    @api.depends("session_line_ids")
    def compute_session_line_count(self):
        for record in self:
            #record.session_line_count = sum([l.token_sys or 0 for l in record.session_line_ids])
            start_date = date.today() - timedelta(days=date.today().day - 1)
            end_date = date(year=date.today().year, month=date.today().month + 1, day=1) - timedelta(days=1)
            filterd_line_ids = list(filter(lambda session_line_id: session_line_id.datetime.date() >= start_date and session_line_id.datetime.date() <= end_date, record.session_line_ids))
            record.session_line_count = sum([f.token_sys or 0 for f in filterd_line_ids])

    @api.depends("session_ids")
    def compute_session_count(self):
        for record in self:
            record.session_count = len(record.session_ids)

    @api.depends("session_line_ids")
    def compute_agent_count(self):
        for record in self:
            ai_agent_ids = list(map(lambda session_id: session_id.ai_agent_ids.ids, self.session_ids))
            agent_ids = []
            [agent_ids.extend(ai_agent_id) for ai_agent_id in ai_agent_ids]
            record.agent_count = len(set(agent_ids))

    @api.depends('model_id')
    def _compute_model_name(self):
        for record in self:
            record.model_name = record.model_id.model if record.model_id else False

    @api.onchange('model_id')
    def _onchange_model_id(self):
        if self.init_type == 'server-action':
            if self.server_action_id:
                self.server_action_id.write({
                    'name': self.name,
                    'model_id': self.model_id.id,
                    'binding_model_id': self.model_id.id if self.status == 'active' else None,
                    "binding_view_types": "form,list",
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
        model = self.model_id.name
        qtype = _('AI Staff') if self.ai_type == 'ai-staff' else _('Quest')
        self.init_type_str = _(f'This {qtype} will begin work when you press START button')

        # ~ if self.init_type != 'cron' and self.cron_id:
        # ~ self.cron_id.unlink()
        if self.init_type == 'cron':
            self.init_type_str = _(f'This {qtype} will begin work at a schedule, \nfollow the schedule for updating')
            if not self.cron_id:
                self.cron_id = self.cron_id.create({
                    'name': self.name,
                    'model_id': self.model_id.id if self.model_id else self.env.ref('base.model_res_partner').id,
                    'state': 'code',
                    'code': f"action = env.ref('{self._get_eid()}').cron(records)",
                })
        # ~ if self.init_type != 'server-action' and self.server_action_id:
        # ~ self.server_action_id.unlink()

        if self.init_type == "mail":
            self.init_type_str = _(f'This {qtype} will begin work when receiving a mail at this\naddress')
            if not self.alias_name:
                self.alias_name = self.name

        if self.init_type == 'server-action':
            self.init_type_str = _(f'Visit {model} or use checkboxes to apply this \naction to the model.')
            if not self.server_action_id:
                self.server_action_id = self.server_action_id.create({
                    'name': self.name,
                    'model_id': self.model_id.id if self.model_id else self.env.ref('base.model_res_partner').id,
                    "binding_view_types": "form,list",
                    'state': 'code',
                    'code': f"action = env.ref('{self._get_eid()}').server_action(records)",
                })
        # ~ if self.init_type != 'channel' and self.channel_id:
        # ~ self.channel_id.unlink()

        if self.init_type == 'channel':
            self.init_type_str = _(
                f'Chat with this bot at the channel, the dialog is public for all members of this channel')
            if not self.channel_id:
                self.channel_id = self.channel_id.create({
                    'name': self.name,
                    'ai_quest_id': self.id,
                })
        # ~ if self.init_type != 'chat' and self.chat_user_id:
        # ~ self.chat_user_id.unlink()

        if self.init_type == 'chat':
            self.init_type_str = _(f'Chat with this bot, the dialog is private for you and the bot')
            if not self.chat_user_id:
                user_vals = {
                    'name': self.name,
                    'login': self.name,
                    'ai_quest_id': self._origin.id or self.id
                }
                self.chat_user_id = self._get_or_set_chat_user(user_vals).id
        self.name = name

    def _get_or_set_chat_user(self, user_vals):
        res_user = self.env['res.users']
        if user_id := res_user.search([('name', '=', user_vals.get('name'))], limit=1):
            user_id.write({'ai_quest_id': user_vals.get('ai_quest_id')})
            return user_id
        else:
            return res_user.with_context(no_reset_password=True).create(user_vals)

    def _get_eid(self):
        if not self.name:
            raise ValidationError("Set a name for this quest")
        eid = list(self.get_external_id().values())[0]
        if not eid:
            eid_name = unidecode.unidecode(re.sub(
                r'[^a-zA-Z0-9åäö\s]', '', self.name.lower()
            ).replace(' ', '_')) + f"_{int(''.join(filter(str.isdigit, str(self.id))))}"
            eid = self.env['ir.model.data'].search([('name', '=', eid_name)], limit=1)
            if not eid:
                self.env['ir.model.data'].create({
                    'name': eid_name,
                    'module': 'new',
                    'model': 'ai.quest',
                    'res_id': self.id,
                })
        return eid

    def log_message(self, body, is_error=False):
        if self.id:  # Ensure the record exists and has an ID
            self.message_post(
                body=body,
                subtype_xmlid='mail.mt_note',  # Log note subtype
                message_type='comment',
            )
        else:
            # Use message_notify for transient models or notifications
            self.env['mail.thread'].sudo().message_notify(
                partner_ids=[self.env.user.partner_id.id],
                subject="Log Message" if not is_error else "Error Log",
                body=body,
                message_type='notification',
            )

    def mail_test_wizard(self):
        if self._check_quest_error():
            raise UserError(self._check_quest_error())
        action = self.env.ref("ai_agent.action_ai_quest_test_mail_wizard").read()[0]
        action["context"] = {"default_ai_quest_id": self.id}
        return action
        
    def start(self):
        pass

    # ------------------------------------------------------------
    # Init type API
    # ------------------------------------------------------------
    def _check_quest_error(self):
        if len(self.ai_agent_ids) == 0:
            raise UserError(_('You have to assign at least one agent to the quest'))
        if len(self.ai_agent_ids.filtered(lambda a: a.ai_agent_id.status != 'active')) > 0:
            raise UserError(_('Check status on agents'))
        if len(self.ai_agent_ids.filtered(lambda a: a.ai_agent_id.ai_agent_llm_id == False)) > 0:
            raise UserError(_('Missing LLM on agent'))
        if len(self.ai_agent_ids.filtered(lambda a: a.ai_agent_id.ai_agent_llm_id == False)) > 0:
            raise UserError(_('Missing LLM on agent'))
        if len(self.ai_agent_ids.filtered(lambda a: a.ai_agent_id.ai_agent_llm_id.status != 'confirmed')) > 0:
            raise UserError(_('Check status on LLMs'))
        if len(self.ai_agent_ids.filtered(
                lambda a: a.ai_agent_id.ai_agent_llm_id.is_key_required and not a.ai_agent_id.ai_agent_llm_id.ai_api_key
        )) > 0:
            pass
            # return _('Missing API Key on LLMs')
        if self.code == DEFAULT_PYTHON_CODE:
            raise UserError(_('Missing Python Code on the quest'))
        if not self.description:
            raise UserError(_('Missing Description on the quest'))
        return False

    def _server_action_values(self, **kwargs):
        return kwargs

    def server_action(self, records):
        vals = self._server_action_values(records=records)

        if self.init_type == 'server-action' and self.server_action_id:
            if self._check_quest_error():
                raise UserError(self._check_quest_error())

        res = self.run(**vals)
        self.log_message(f'server-action {res}')
        return res

    def _cron_values(self, **kwargs):
        return kwargs

    def cron(self, records):
        self.ensure_one()
        self_sudo = self.sudo()
        if self.init_type == 'cron' and self.cron_id:
            if self._check_quest_error():
                self.log_message(self._check_quest_error())

            if self.filter_domain:
                # domain = safe_eval(self_sudo.filter_domain, self._get_eval_context())
                domain = safe_eval(self_sudo.filter_domain)
                records = self.env[self.model_id.model].search(domain)
            else:
                records = records
            vals = self._cron_values(records=records)
            result = self.run(**vals)
            _logger.info(f"cron job {result=}")

    def _chat_values(self, **kwargs):
        return kwargs

    def chat(self, message, channel, bot_user):
        
        """"
            Implements chat with channel and bot
            
            code:
            
            result = quest.build_graph(session=session,message=message).invoke(message_invoke)
            
            
        """
        if (self.init_type == 'chat' and self.chat_user_id) or (self.init_type == "channel" and self.channel_id):
            self = self.sudo()
            if self._check_quest_error():
                raise UserError(self._check_quest_error())
            self.write({'real_channel_id': channel.id, 'real_chat_user_id': bot_user.id})
            session = message.parent_id.ai_quest_session_id if message.parent_id and message.parent_id.ai_quest_session_id else \
                message.parent_id.ai_quest_session_id if message.ai_quest_session_id else \
                    self.env['ai.quest.session'].quest_init(self)
            vals = self._chat_values(session=session, message=message, channel=channel, bot_user=bot_user)
            res = self.run(**vals)
            return res

    def _mail_values(self, **kwargs):
        return kwargs

    def mail(self, mail, session):
        if self.init_type == "mail":
            if self._check_quest_error():
                self.log_message(self._check_quest_error())
            mail_body = html2plaintext(self.markdown2html(mail.body)).replace("<b>", "").replace("</b>", "").replace(
                "<br>", "").replace("<p>", "").replace("</p>", "").replace("\n", "")
            vals = self._mail_values(mail=mail, mail_body=mail_body, session=session, attachments=mail.attachment_ids)
            res = self.run(**vals)
            return res

    def powerbox(self, quest, prompt, res_model, res_id):
        ai_quest = self.env['ai.quest'].browse(quest.get('id')).exists()
        if not ai_quest:
            raise UserError(_("OBS: Quest does not exist, you should contact administrator to look into the quest"))
        if res_model and res_id:
            record = self.env[res_model].browse(int(res_id)).exists()  # Instantiate record here
        else:
            record = False
        result = ai_quest.run(prompt=prompt, record=record)

        if result:
            ai_messages = self._get_last_ai_message(result.get('result', {}).get('messages', False))
            if not ai_messages:
                raise UserError(_("OBS: An error occurred, you should contact administrator to look into the quest"))

            if ai_quest.debug:
                answer = markdown.markdown(ai_messages.content)
            else:
                answer = re.sub(
                    r'<think>.*?</think>', '', markdown.markdown(ai_messages.content), flags=re.DOTALL)

            return answer
        raise UserError(_("OBS: An error occurred, you should contact administrator to look into the quest"))

    # ------------------------------------------------------------
    # Python code helpers
    # ------------------------------------------------------------

    @api.model
    def is_ai_message(self, var):
        return isinstance(var, AIMessage)

    @api.model
    def get_last_ai_message_content(self, response):
        if response.get('messages', False):
            messages = response.get('messages', [])
            ai_messages = [m for m in messages if self.is_ai_message(m)]
            if ai_messages:
                last_ai_message = ai_messages[-1] if len(ai_messages) != 0 else None
                if messages and last_ai_message:
                    _logger.error(f"{last_ai_message=}")
                    return last_ai_message.content

    @api.model
    def extract_dicts(self, text):
        # Regular expression to match JSON-like structures
        pattern = r'\{[^}]+\}'

        # Find all matches
        matches = re.findall(pattern, text)

        # Parse each match into a dictionary
        result = []
        for match in matches:
            try:
                # Replace single quotes with double quotes for valid JSON
                json_str = match.replace("'", '"')
                # Parse the JSON string
                data = json.loads(json_str)
                result.append(data)
            except json.JSONDecodeError:
                _logger.error(f"Failed to parse: {match}")

        return result

    @api.model
    def markdown2html(self, text):
        return markdown.markdown(text)
    
    @api.model
    def json2dict(self, text):
        """Safely extract and parse JSON or dictionary-like content from text."""
        # First attempt: Look for JSON content within code blocks
        json_match = re.search(r'```(?:json)?\s*([\s\S]*?)```', text)
        if json_match:
            json_content = json_match.group(1).strip()
            try:
                return json.loads(json_content)
            except json.JSONDecodeError:
                # Try some common JSON cleanup operations
                cleaned = json_content.replace('\\\'', '\'')  # Handle escaped single quotes
                cleaned = cleaned.replace('\n', ' ')  # Remove newlines within the JSON
                cleaned = re.sub(r'\\n', '\\\\n', cleaned)  # Properly escape \n sequences

                try:
                    return json.loads(cleaned)
                except json.JSONDecodeError:
                    pass  # Move to next method if this fails

        # Second attempt: Try to find a dictionary-like structure
        dict_match = re.search(r'\{[\s\S]*?}', text)
        if dict_match:
            dict_text = dict_match.group(0)
            try:
                # Try direct JSON parsing first
                return json.loads(dict_text)
            except json.JSONDecodeError:
                # If JSON parsing fails, try to convert to valid Python dict syntax
                py_dict = dict_text.replace(
                    'null', 'None'
                ).replace('true', 'True').replace('false', 'False')
                try:
                    # Use safer ast.literal_eval instead of eval
                    return ast.literal_eval(py_dict)
                except (SyntaxError, ValueError):
                    pass  # Move to next method if this fails

        # Third attempt: As a last resort, try ast.literal_eval on the entire text
        # This is much safer than using eval()
        try:
            return ast.literal_eval(text)
        except (SyntaxError, ValueError):
            pass

        # If all parsing attempts fail, return an empty dictionary
        return {}

    # ------------------------------------------------------------
    # Python CODE eval
    # ------------------------------------------------------------

    def _get_eval_context(self, action=None, kw=None):
        """ Prepare the context used when evaluating python code, like the
        python formulas or code server actions.

        :param action: the current server action
        :type action: browse record
        :returns: dict -- evaluation context given to (safe_)safe_eval """
        records = kw.get('records', [])
        message = kw.get('message', False)
        prompt = kw.get('prompt', '')
        message_body = html2plaintext(message.body) if message else prompt
        
        eval_context = {
            'action': action,
            'env': self.env,
            'self': self,
            'session': kw.get('session', self.env['ai.quest.session'].quest_init(self)),
            # 'session': kw.get('session'),
            'quest': self,
            'agents': [a.ai_agent_id for a in self.ai_agent_ids],
            'company_id': self.env.user.company_id,
            'context': self.env.context,
            'record': records[0] if records else None,
            'records': records,
            # context
            'message_body': message_body,
            'message_invoke': {"messages": [HumanMessage(content=message_body)]},
            # Exceptions
            # #if VERSION <= "16.0"
            'Warning': Warning,
            # #endif
            'UserError': UserError,
            # helpers
            '_logger': _logger,
            'html2plaintext': html2plaintext,
            'HumanMessage': HumanMessage,
            'markdown2html': markdown.markdown,
            **kw,
        }
        return eval_context

    def run(self, **kwargs):
        if self.debug:
            _logger.warning(f" RUN {kwargs=}")
        local_dict = {}
        try:
            if self.debug:
                _logger.warning(f" Före eval context -----------------------")
            eval_context = self._get_eval_context(None, kwargs)
            # eval_context["session"].status = "active"
            if self.debug:
                _logger.warning(f"Efter eval context {eval_context=}" + f"{self.code=}\n=======\n {local_dict=}")
            safe_eval(self.code, eval_context, local_dict, mode="exec", nocopy=True)
        except ValueError as e:
            self.log_message(f"ValueError {e=}", is_error=True)
            if self.debug:
                self.log_message(f"{e=}\n\n=====\n{self.code=}\n=======\n {local_dict=}\n{traceback.format_exc()}")
            return None
        except Exception as e:
            _logger.error(f"{e=}")
            self.log_message(f" {e=}  {traceback.format_exc()}")
            if self.debug:
                self.log_message(f"{e=}\n\n=====\n{self.code=}\n=======\n {local_dict=}")
            return None
        session = local_dict.get('session', eval_context['session'])

        result = None
        if local_dict.get('result'):
            messages = local_dict.get('result', {}).get('messages', [])
            result = self._get_last_ai_message(messages)
        elif local_dict.get('response'):
            messages = local_dict.get('response', {}).get('messages', [])
            result = self._get_last_ai_message(messages)

        if not isinstance(result, list):
            result = [result]

        _logger.error(f"{result=}")

        objects = {
            'ai_session_id': eval_context.get('session'),
            'ai_quest_id': eval_context.get('self'),
            'records': eval_context.get('records')
        }

        if result:
            session.store_session_data(result=result, objects=objects)

        return local_dict

    # ------------------------------------------------------------
    # ORM
    # ------------------------------------------------------------

    def _get_last_ai_message(self, messages):
        ai_messages = [m for m in messages if isinstance(m, AIMessage)]
        result = ai_messages[-1] if ai_messages else None
        return result

    def _alias_get_creation_values(self):
        values = super(AIQuest, self)._alias_get_creation_values()
        values['alias_model_id'] = self.env['ir.model']._get('ai.quest.session').id
        if self.id:
            values['alias_defaults'] = defaults = {}
            defaults['ai_quest_id'] = self.id
            defaults['status'] = 'active'
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
            if quest.server_action_id and quest.init_type == "server-action":
                quest.server_action_id.write(
                    {'name': quest.name, 'code': f"action = env.ref('{quest._get_eid()}').server_action(records)",
                     "binding_view_types": "form,list",
                     'binding_model_id': self.model_id.id if self.status == 'active' else None})
            if quest.cron_id:
                quest.cron_id.write({'name': quest.name, 'code': f"action = env.ref('{quest._get_eid()}').cron(records)"})
            if quest.channel_id:
                quest.channel_id.write({'name': quest.name, 'ai_quest_id': quest.id, })
            if quest.chat_user_id:
                quest.chat_user_id.write({'name': quest.name, 'login': quest.name, 'ai_quest_id': quest.id, })
        return result

    @api.model_create_multi
    def create(self, vals_list):
        _logger.error(f"{vals_list=}")
        new_server_action = False
        for record in vals_list:
            if not record.get("user_id"):
                record.update({"user_id": self.env.user.id})
            if record.get("model_id", False):
                new_server_action = self.server_action_id = self.server_action_id.create({
                    'name': record["name"],
                    'model_id': record["model_id"],
                    "binding_view_types": "form,list",
                    'state': 'code',
                    'code': "",
                })
        res = super(AIQuest, self).create(vals_list)
        if new_server_action:
            new_server_action.write({"code": f"action = env.ref('{res._get_eid()}').server_action(records)"})
            res.write({"server_action_id": new_server_action.id})
        return res

    # ------------------------------------------------------------
    # LangGraph 
    # ------------------------------------------------------------

    def build(self, mermaid=True,**kwargs):
        kwargs.update({"mermaid": mermaid})
        if self.is_supervisor:
            _logger.info(f"Building graph with supervisor ")
            return self.build_supervisor(**kwargs)
        else:
            _logger.info(f"Building chain ")
            return self.build_chain(**kwargs)

    def build_supervisor(self, **kwargs):
        """Build a multi-agent workflow graph with supervisor."""
        if not self.ai_agent_ids:
            raise ValueError("No agents provided")

        agents = [line.ai_agent_id for line in self.ai_agent_ids]

        # Get member names
        members = [a.get_agent_name(i, **kwargs) for i, a in enumerate(agents)]
        _logger.info(f"Building graph with supervisor and {len(members)} workers: {members}")

        # Get session from kwargs
        session = kwargs.get('session')
        if not session:
            raise UserError(_("No session added to build_graph method"))

        try:
            # Create graph
            graph_builder = StateGraph(AgentState)

            # Add supervisor
            _logger.info(f"Adding supervisor with {members=}")
            graph_builder.add_node(
                self.get_agent_name(**kwargs),
                self.create_supervisor_node(members, **kwargs)
            )

            # Add worker nodes
            for i, agent in enumerate(agents):
                graph_builder.add_node(agent.get_agent_name(i, **kwargs), agent.create_node(**kwargs))

            # Add edges from workers to supervisor
            for member in [a.get_agent_name(i, **kwargs) for i, a in enumerate(agents)]:
                _logger.info(f"Adding edge: {member} -> Supervisor")
                graph_builder.add_edge(member, self.get_agent_name(**kwargs))

            # Add conditional routing
            conditional_map = {k: k for k in [a.get_agent_name(i, **kwargs) for i, a in enumerate(agents)]}
            conditional_map["FINISH"] = END

            _logger.info("Adding conditional edges with routes: " +
                         ", ".join([f"{k} -> {v}" for k, v in conditional_map.items()]))

            graph_builder.add_conditional_edges(
                self.get_agent_name(**kwargs),
                lambda x: x["next"],
                conditional_map
            )

            # Set entry point
            graph_builder.set_entry_point(self.get_agent_name(**kwargs))

            # Compile and return
            _logger.info("Compiling graph")

            graph = graph_builder.compile()

            # if self.debug:
            #     self.log_message(f"Graph structure: {json.dumps(graph.get_graph().to_json(), indent=2)}")
            #     _logger.debug(f"Graph structure: {json.dumps(graph.get_graph().to_json(), indent=2)}")

            return graph

        except Exception as e:
            self.log_message(f"Error building graph: {str(e)}", is_error=True)
            _logger.error(f"Error building graph: {str(e)}\n{traceback.format_exc()}")
            raise

    def create_supervisor_node(self, members, **kwargs):
        """Create a supervisor node that coordinates between different agents."""
        # Get session from kwargs
        session = kwargs.get('session')
        if not session:
            raise UserError(_("No session provided to supervisor node"))

        use_lang = f"Use language {self.env.user.lang} for the answer to Human" if self.use_personal_lang else ''
        topic = kwargs.get('topic', kwargs.get('message', ''))
        quest_description = self.description

        if kwargs.get('record'):  # Populate with data from record if there is a record
            try:
                data = kwargs.get('record').read()[0]
                quest_description = quest_description.format(**{k: data[k] for k in data.keys()})
            except Exception as e:
                _logger.warning(f"Error formatting quest description with record data: {e}")

        # Format the supervisor prompt with required parameters
        system_prompt = """You are a supervisor coordinating between workers: {members}.
    Based on the request, determine which worker should handle the next step.
    Choose FINISH ONLY when a complete response has been provided.

    Guidelines: {guidelines}

    Instructions:
    1. Carefully review all previous messages and the current state
    2. If the response is NOT complete, choose the most appropriate worker
    3. Choose FINISH ONLY when we have a satisfactory, complete response
    4. Be explicit in your decision - name the exact worker or say FINISH
    5. If no worker is making progress after multiple attempts, choose a different worker
    6. {use_lang}

    IMPORTANT: Never decide FINISH on the first round unless the request is trivially simple.
    """

        system_prompt = system_prompt.format(
            members=", ".join(members),
            guidelines=quest_description,
            use_lang=use_lang
        )

        system_prompt += f"\n\n{self._extra_context()}"

        def supervisor_chain(state):
            """Process state and decide which agent should act next."""
            try:
                messages = state.get('messages', [])
                latest_message = ""

                # Track cycles to prevent infinite loops
                if 'cycle_count' not in state:
                    state['cycle_count'] = 0
                else:
                    state['cycle_count'] += 1

                # Force FINISH if we're cycling too much
                if state['cycle_count'] > self.supervisor_cycles:  # Adjust threshold as needed
                    session.add_message(f"Forcing FINISH after {state['cycle_count']} cycles")
                    return {"next": "FINISH", 'session': session}

                # Handle different message formats
                if messages:
                    if isinstance(messages[-1], dict) and 'content' in messages[-1]:
                        latest_message = messages[-1]['content']
                    elif hasattr(messages[-1], 'content'):
                        latest_message = messages[-1].content
                    elif isinstance(messages[-1], str):
                        latest_message = messages[-1]
                    else:
                        # Try to convert to string
                        latest_message = str(messages[-1])

                if self.debug:
                    session.add_message(f"SUPERVISOR state with members: {members}")
                    session.add_message(f"Latest message: {latest_message[:100]}...")

                # Initialize scratchpad if needed
                if 'scratchpad' not in state or state['scratchpad'] is None:
                    state['scratchpad'] = []
                elif isinstance(state['scratchpad'], str):
                    state['scratchpad'] = [state['scratchpad']]

                # If this is the very first message and no previous interaction
                if state['cycle_count'] == 0 and len(messages) == 1:
                    first_worker = members[0] if members else "FINISH"
                    session.add_message(f"Initial request, starting with: {first_worker}")
                    return {
                        "next": first_worker,
                        'session': session,
                        'messages': messages,
                        'cycle_count': state['cycle_count']
                    }

                # Prepare prompt for the supervisor
                prompt = f"Previous input: {latest_message}\n\n"
                prompt += f"Based on the previous input, which agent should act next? Choose from: {members} or say FINISH if we have a complete response."
                prompt += (f"\n\nImportant: You must choose EXACTLY one of these agents by name or say FINISH. Do not "
                           f"modify the agent names.")

                if self.debug:
                    session.add_message(f"Supervisor prompt: {prompt}")

                # Apply rate limiting before using supervisor LLM
                if self.supervisor_llm_id:
                    try:
                        # Check rate limits
                        if not self.supervisor_llm_id.check_rate_limits(input_text=prompt):
                            return False
                    except UserError as e:
                        # Rate limit exceeded
                        error_msg = f"Rate limit exceeded for supervisor: {str(e)}"
                        _logger.warning(error_msg)
                        session.add_message(error_msg)

                        # Return FINISH on rate limit to avoid getting stuck
                        return {"next": "FINISH", 'session': session}

                # Get LLM for supervisor
                llm = self.supervisor_llm_id.get_llm(temperature=self.supervisor_temperature)

                messages_to_llm = [
                    SystemMessage(content=system_prompt),
                    HumanMessage(content=prompt)
                ]

                # Get supervisor decision
                response = llm.invoke(messages_to_llm)
                content = response.content

                if self.debug:
                    session.add_message(f"Supervisor raw response: {content}")

                # Extract the decision from the response
                decision = self.extract_supervisor_decision(content, session=session, members=members)

                if decision == "FINISH":
                    _logger.info("Supervisor decided to FINISH")
                    session.add_message("Supervisor decided to FINISH")

                    # Ensure we return an AI message as the final result
                    if 'messages' in state and state['messages'] and isinstance(state['messages'][-1], AIMessage):
                        final_message = state['messages'][-1]
                    else:
                        final_message = AIMessage(content="Task complete. " + (latest_message or ""))
                        if 'messages' not in state:
                            state['messages'] = [final_message]
                        else:
                            state['messages'].append(final_message)

                    return {
                        "next": "FINISH",
                        'session': session,
                        'messages': state['messages']
                    }

                # Verify the decision is a valid agent name
                if decision not in members:
                    # Try to find the closest match
                    closest_match = None
                    for member in members:
                        if decision.lower() in member.lower() or member.lower() in decision.lower():
                            closest_match = member
                            break

                    if closest_match:
                        decision = closest_match
                        session.add_message(f"Matched '{decision}' to agent: {closest_match}")
                    elif len(members) > 0:
                        # Default to first agent if no match
                        decision = members[0]
                        session.add_message(f"No match for '{decision}', defaulting to: {members[0]}")
                    else:
                        session.add_message(f"No valid agents found, finishing")
                        return {"next": "FINISH", 'session': session}

                session.add_message(f"Supervisor selected: {decision}")
                return {
                    "next": decision,
                    'session': session,
                    'messages': state.get('messages'),
                    'cycle_count': state['cycle_count']
                }

            except Exception as e:
                _logger.error(f"Error in supervisor chain: {str(e)}", exc_info=True)
                session.add_message(f"Error in supervisor chain: {str(e)}\n{traceback.format_exc()}")
                return {"next": "FINISH", 'session': session}

        return supervisor_chain

    def extract_supervisor_decision(self, content, **kwargs):
        """Extract the agent decision from the supervisor's response."""
        session = kwargs.get('session')
        members = kwargs.get('members', [])

        # First, check for JSON format
        try:
            json_match = re.search(r'\{.*?"next"\s*:\s*"([^"]+)".*?}', content, re.DOTALL)
            if json_match:
                decision = json_match.group(1)
                return self._match_agent_name(decision, members)
        except Exception as e:
            _logger.warning(f"Error extracting JSON with regex: {e}")

        # Check for explicit FINISH
        if re.search(r'\bFINISH\b', content, re.IGNORECASE):
            return "FINISH"

        # Look for agent mentioned with specific patterns
        intent_patterns = [
            r'(?:next agent should be|choose|select|I choose|I select|I recommend)\s*[":]*\s*([A-Za-z0-9_\s\-\.,]+)',
            r'([A-Za-z0-9_\s\-\.,]+)(?:\s+for the next step|\s+should handle|\s+is best|\s+would be appropriate)',
            r'I think\s+([A-Za-z0-9_\s\-\.,]+)\s+should',
        ]

        for pattern in intent_patterns:
            match = re.search(pattern, content, re.IGNORECASE)
            if match:
                decision = match.group(1).strip()
                return self._match_agent_name(decision, members)

        # If no clear decision was found, check if any agent name is mentioned
        for member in members:
            if re.search(r'\b' + re.escape(member.split('\n')[0]) + r'\b', content, re.IGNORECASE):
                return member

        # Check for completion indicators
        completion_patterns = [
            r'task\s+complete', r'complete\s+response', r'answer\s+is\s+complete',
            r'no\s+further\s+action', r'satisfactory\s+response', r'response\s+is\s+complete'
        ]
        if any(re.search(pattern, content, re.IGNORECASE) for pattern in completion_patterns):
            return "FINISH"

        # Default to first agent if we can't determine
        if len(members) > 0:
            return members[0]
        return "FINISH"

    def _match_agent_name(self, decision, members):
        """Match a decision string to the closest agent name."""
        # Direct match
        if decision in members:
            return decision

        if decision.upper() == "FINISH":
            return "FINISH"

        # Clean the decision (remove formatting, newlines)
        decision = decision.strip().split('\n')[0]

        # Case-insensitive match
        for member in members:
            member_name = member.split('\n')[0] if '\n' in member else member
            if decision.lower() == member_name.lower():
                return member

        # Partial match - check if decision contains a member name
        for member in members:
            member_name = member.split('\n')[0] if '\n' in member else member
            if member_name.lower() in decision.lower():
                return member

        # Check if any member name contains the decision
        for member in members:
            member_name = member.split('\n')[0] if '\n' in member else member
            if decision.lower() in member_name.lower():
                return member

        # If all else fails, return first member or FINISH
        if members:
            return members[0]
        return "FINISH"

    # Helper method to safely get text from messages
    def _get_message_content(self, message):
        """Safely extract content from different message formats"""
        if message is None:
            return ""
        if isinstance(message, dict) and 'content' in message:
            return message['content']
        elif hasattr(message, 'content'):
            return message.content
        elif isinstance(message, str):
            return message
        return str(message)  # Try to convert to string

    def extra_context(self):
        from datetime import datetime
        res = {}
        if self.use_company_info:
            res[
                'company_info'] = f'Company information: {self.env.user.company_id.company_mission=} {self.env.user.company_id.company_values=}'
        if self.use_personal_info:
            res[
                'user_info'] = f'User information: {self.env.user.name=} {self.env.user.function=} {self.env.user.city=}'
        if self.use_time_context:
            now = datetime.now()
            res[
                'time_context'] = f'Current date {now.strftime("%Y-%m-%d")} Current time {now.strftime("%H:%M:%S")} Week Number {now.isocalendar()[1]}\n'
        return res

    def _extra_context(self):
        res = ''
        for key, data in self.extra_context().items():
            res += data
        return res

    # https://www.perplexity.ai/search/i-langgraph-vill-jag-komma-at-fCisIUB7RjaKwPovE_fyZg

    def build_chain(self, **kwargs):
        """Build a sequential chain of agents."""
        if not self.ai_agent_ids:
            raise ValueError("No agents provided")

        # Get agents sorted by sequence
        agents = [agent for agent in self.ai_agent_ids.sorted(key=lambda s: s.sequence).mapped('ai_agent_id') if agent]

        # Get session
        session = kwargs.get('session', False)
        if not session:
            raise UserError(_("No session provided to build_chain"))

        # Set debug mode
        debug = kwargs.get('debug', self.debug)

        if debug:
            session.add_message(f"Building chain with {len(agents)} agents")

        def initial_node(state):
            """Initialize the state for the chain."""
            # Get the initial message/topic
            initial_message = kwargs.get('topic', kwargs.get('message', ''))

            #TODO Fixa extra context T/0463

            if debug:
                session.add_message(f"Initializing chain with message: {initial_message[:100]}...")

            # Create a proper HumanMessage
            human_message = HumanMessage(content=initial_message)

            _logger.error(f"{human_message=}")

            return {
                "messages": [human_message],
                'quest': self,
                'session': session,
                'topic': initial_message,
                'scratchpad': [],
                'cycle_count': 0,
                'current_agent': 'initial_node',
                'sequence_position': 0,
                'last_position': len(agents),
            }

        try:
            # Create the graph
            graph = StateGraph(AgentState)

            # Add initial node
            graph.add_node("initial", initial_node)
            graph.add_edge(START, "initial")

            # Add agent nodes and edges between them
            for i, agent in enumerate(agents):
                node_name = agent.get_agent_name(i, **kwargs)

                if debug:
                    session.add_message(f"Adding agent node: {node_name}")

                # Add the node
                graph.add_node(node_name, agent.create_node(
                    debug=debug,
                    current_agent=agent.name,
                    quest=self,
                    **kwargs
                ))

                # Connect nodes
                if i == 0:
                    # Connect initial node to first agent
                    graph.add_edge("initial", node_name)
                else:
                    # Connect previous agent to current agent
                    prev_node = agents[i - 1].get_agent_name(i - 1, **kwargs)
                    graph.add_edge(prev_node, node_name)

            # Connect last agent to END
            graph.add_edge(agents[-1].get_agent_name(len(agents) - 1, **kwargs), END)

            # Set entry point
            graph.set_entry_point("initial")

            # Compile the graph
            compiled_graph = graph.compile()

            return compiled_graph

        except Exception as e:
            error_msg = f"Error building chain: {str(e)}\n{traceback.format_exc()}"
            _logger.error(error_msg)
            session.add_message(error_msg)
            raise

    # Inspired by https://github.com/menonpg/agentic_search_openai_langgraph/blob/main/agents.py
    def build_graph(self, **kwargs):
        """Build a multi-agent workflow graph with supervisor."""
        raise UserError('quest.build_graph() is outdated, use quest.build() instead')

    def extract_json(self, agent, session, message):
        # Find JSON-like content within triple backticks
        message = message.replace('\n', '').replace("'", '"')
        json_match = re.search(r'``````', message, re.DOTALL)
        if json_match:
            json_string = json_match.group(1)
            try:
                return json.loads(json_string)
            except json.JSONDecodeError:
                session.add_message(f"{agent} Error: Invalid JSON format with backtick {json_string=}")
                return None
        else:
            try:
                return json.loads(message)
            except json.JSONDecodeError:
                session.add_message(f"{agent} Error: Invalid JSON format\n{message}")
            try:
                return json.loads(message)
            except json.JSONDecodeError:
                session.add_message(f"{agent} Error: Invalid JSON tried updated '''json {message}'''")

    def get_agent_name(self, **kwargs):
        if kwargs.get('mermaid'):
            llm = re.sub(
                r'[\'()\[\]{}:]', '_', self.supervisor_llm_id.name
            ).replace(
                ' ',
                ''
            ) if self.supervisor_llm_id and self.supervisor_llm_id.name else ''
            supervisor = f"Supervisor\n<small>fa&colon;fa-cog {llm}</small>"
            # ~ _logger.info(f"Supervisor ------------------>{supervisor}")
            return supervisor
        else:
            return "Supervisor"



class AgentState(TypedDict):
    messages: Annotated[Sequence[BaseMessage], operator.add]
    session: Optional[SkipValidation[AIQuestSession]] = None
    quest: Optional[SkipValidation[AIQuest]] = None
    topic: str
    scratchpad: Annotated[List[str], operator.add]
    next: str
    token: Optional[int] = None
    current_agent: str
    sequence_position: int
    last_position: int
    cycle_count: int
