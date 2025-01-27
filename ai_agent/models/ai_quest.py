
import base64
import json
import logging
import markdown
import operator
import re
import traceback
import unidecode
import warnings

warnings.filterwarnings("ignore", category=DeprecationWarning,
                        message="builtin type swigvarlink has no __module__ attribute")

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langgraph.graph import END, StateGraph
from odoo import models, fields, api, _
from odoo.addons.ai_agent.models.ai_quest_session import AIQuestSession
from odoo.exceptions import UserError, ValidationError, Warning
from odoo.tools.mail import html2plaintext
from odoo.tools.safe_eval import safe_eval
from random import randint
from secrets import choice
from typing_extensions import Annotated, TypedDict, Sequence

from odoo.addons.base.models.avatar_mixin import get_hsl_from_seed

_logger = logging.getLogger(__name__)

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


class AgentState(TypedDict):
    messages: Annotated[Sequence[BaseMessage], operator.add]
    session: AIQuestSession
    next: str


class AIQuestAgent(models.Model):
    _name = 'ai.quest.agent'
    _description = 'AI Quest Agent'

    ai_quest_id = fields.Many2one(comodel_name='ai.quest', string="", help="")
    sequence = fields.Integer(string='Sequence')
    ai_agent_id = fields.Many2one(comodel_name='ai.agent', string="Agent", help="")
    object_id = fields.Reference(string='Object', related="ai_agent_id.object_id",
                                 selection=lambda m: [(model.model, model.name) for model in
                                                      m.env['ir.model'].sudo().search([])])
    ai_agent_status = fields.Selection(
        selection=[("draft", _("Draft")), ("active", _("Active")), ("done", _("Done")), ("error", _("Error"))],
        default="draft", related='ai_agent_id.status')
    ai_agent_llm_id = fields.Many2one(comodel_name="ai.agent.llm", string="LLM", help="Choose Large Language Model",
                                      domain="[('status','=','confirmed')]", related='ai_agent_id.ai_agent_llm_id')
    ai_llm_status = fields.Selection(
        selection=[("not_confirmed", "Not Confirmed"), ("confirmed", "Confirmed"), ("error", "Error")],
        default="not_confirmed", related='ai_agent_id.ai_agent_llm_id.status')


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
# result = quest.build_graph(session=session,message=message_body).invoke(message_invoke)
#\n\n\n
"""

# Python code
INIT_TYPES = [
    ('manual', 'Manual'),
    ('mail', 'Mail'),
    ('chat', 'Chat with User'),
    ('channel', 'Chat with Channel'),
    ('cron', 'Scheduled Action'),
    ('server-action', 'Server Action')
]


class AIQuest(models.Model):
    _name = 'ai.quest'
    _inherit = ["mail.thread", "mail.activity.mixin", "mail.alias.mixin"]
    _description = 'AI Quest'

    agent_count = fields.Integer(compute="compute_agent_count")
    ai_agent_ids = fields.One2many(comodel_name='ai.quest.agent', inverse_name='ai_quest_id')
    ai_type = fields.Selection(selection=[("default", "Default"), ('ai-programmer', 'AI Programmer')],
                               default="default", required=True)
    alias_id = fields.Many2one(comodel_name='mail.alias', string='Alias', ondelete="restrict", required=True,
                               help="The email address associated with this channel. New emails received will "
                                    "automatically create new leads assigned to the channel.")
    alias_user_id = fields.Many2one(comodel_name='res.users', related='alias_id.alias_user_id', readonly=False,
                                    inherited=True, )
    avatar_128 = fields.Image("Avatar", max_width=128, max_height=128, compute='_compute_avatar_128')

    channel_id = fields.Many2one(comodel_name='mail.channel', string="Channel", help="")
    chat_history_limit = fields.Integer(string='Chat History Limit', default=10,
                                        help='Limit the chat history to this number of messages')
    chat_user_id = fields.Many2one(comodel_name='res.users', string="Chat User", help="")
    code = fields.Text(string='Python Code', groups='base.group_system', default=DEFAULT_PYTHON_CODE,
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
    real_channel_id = fields.Many2one(comodel_name='mail.channel', string="Channel",
                                      help="This is the channel chat-method get")
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
    status = fields.Selection(
        selection=[("draft", _("Draft")), ("active", _("Active")), ("done", _("Done")), ("error", _("Error"))],
        default="draft")
    tag_ids = fields.Many2many(comodel_name='product.tag', string='Tags')
    use_chat_history = fields.Boolean(string='Use Chat History', default=True, help='Add chat history to the context')
    use_company_info = fields.Boolean(string='Use Company Info', default=True,
                                      help='Add company mission and values to the context')
    use_personal_info = fields.Boolean(string='Use Personal Info', default=True,
                                       help='Add personal name and other info to the context')
    use_personal_lang = fields.Boolean(string='Use Personal Language', default=True,
                                       help='Set Personas language for the LLM')
    use_time_context = fields.Boolean(string='Use Time Context', default=True,
                                      help='Inform the LLM of current time, date')
    user_id = fields.Many2one(comodel_name='res.users', string="Owner", help="")

    @api.model
    def _generate_random_token(self):
        return ''.join(choice('abcdefghijkmnopqrstuvwxyzABCDEFGHIJKLMNPQRSTUVWXYZ23456789') for _i in range(10))

    uuid = fields.Char('UUID', size=50, default=_generate_random_token, copy=False)

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
        }[self.init_type]
        bgcolor = get_hsl_from_seed(self.uuid)
        avatar = avatar.replace('fill="#875a7b"', f'fill="{bgcolor}"')
        return base64.b64encode(avatar.encode())

    @api.depends('session_line_ids')
    def compute_llm_count(self):
        for record in self:
            record.llm_count = len(set(record.session_line_ids.mapped('ai_llm_id')))

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
        ai_agent_ids = list(map(lambda session_id: session_id.ai_agent_ids.ids, self.session_ids))
        agent_ids = []
        [agent_ids.extend(ai_agent_id) for ai_agent_id in ai_agent_ids]
        agent_ids = list(set(agent_ids))
        action = {
            'name': 'AI Agents',
            'type': 'ir.actions.act_window',
            'res_model': 'ai.agent',
            'view_mode': 'kanban,tree,form',
            'target': 'current',
            'domain': [("id", 'in', agent_ids)]
        }
        return action

    def action_get_session_objects(self):
        action = {
            'name': 'Objetcs',
            'type': 'ir.actions.act_window',
            'res_model': 'ai.session.object',
            'view_mode': 'tree,calendar',
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
            record.session_line_count = sum([l.token_sys or 0 for l in record.session_line_ids])

    @api.depends("session_ids")
    def compute_session_count(self):
        for record in self:
            record.session_count = len(record.session_ids)

    @api.depends("session_line_ids")
    def compute_agent_count(self):
        for record in self:
            record.agent_count = len(set(record.session_line_ids.mapped('ai_agent_id')))

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
                    'code': f"action = env.ref('{self._get_eid()}').cron()",
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
        if is_error:
            self.status = "error"
        self.last_run = fields.Datetime.now()
        self.message_post(body=f"{body} | {self.last_run}", message_type="notification")

    def mail_test_wizard(self):
        if self._check_quest_error():
            raise UserError(self._check_quest_error())
        action = self.env.ref("ai_agent.action_ai_quest_test_mail_wizard").read()[0]
        action["context"] = {"default_ai_quest_id": self.id}
        return action

    # ------------------------------------------------------------
    # Init type API
    # ------------------------------------------------------------
    def _check_quest_error(self):
        if len(self.ai_agent_ids) == 0:
            return _('You have to assign at least one agent to the quest')
        if len(self.ai_agent_ids.filtered(lambda a: a.ai_agent_id.status != 'active')) > 0:
            return _('Check status on agents')
        if len(self.ai_agent_ids.filtered(lambda a: a.ai_agent_id.ai_agent_llm_id == False)) > 0:
            return _('Missing LLM on agent')
        if len(self.ai_agent_ids.filtered(lambda a: a.ai_agent_id.ai_agent_llm_id == False)) > 0:
            return _('Missing LLM on agent')
        if len(self.ai_agent_ids.filtered(lambda a: a.ai_agent_id.ai_agent_llm_id.status != 'confirmed')) > 0:
            return _('Check status on LLMs')
        if len(self.ai_agent_ids.filtered(
                lambda a: a.ai_agent_id.ai_agent_llm_id.is_key_required and not a.ai_agent_id.ai_agent_llm_id.ai_api_key
        )) > 0:
            return _('Missing API Key on LLMs')
        # if self.status != 'active':
        #     return _('Wrong status on the quest')
        if self.code == DEFAULT_PYTHON_CODE:
            return _('Missing Python Code on the quest')
        if not self.description:
            return _('Missing Description on the quest')
        return False

    def start(self):
        pass

    def _server_action_values(self, **kwargs):
        return kwargs

    def server_action(self, records):
        if self.init_type == 'server-action' and self.server_action_id:
            if self._check_quest_error():
                raise UserError(self._check_quest_error())
            vals = self._server_action_values(records=records)
            res = self.run(records=records)
            self.log_message(f'server-action {res}')

            #     vals = self._server_action_values(records=records)
            #     if self.code:
            #         return self.with_context({'records': records, 'session': vals['session']}).run()
            #     else:
            #         return vals['agent'].prompt_agent('',session=vals['session'])
            #

    def _cron_values(self, **kwargs):
        return kwargs

    def cron(self, records):
        self.ensure_one()
        self_sudo = self.sudo()
        if self.init_type == 'cron' and self.cron_id:
            if self._check_quest_error():
                self.log_message(self._check_quest_error())
            if self.filter_domain:
                domain = safe_eval.safe_eval(self_sudo.filter_domain, self._get_eval_context())
                records = self.env[self.model_id.model].search(domain)
            else:
                records = {}
            vals = self._cron_values(records=records)
            result = self.run(**vals)

    def _chat_values(self, **kwargs):
        return kwargs

    def chat(self, message, channel, bot_user):
        """"
            Implements chat with channel and bot
            
            code:
            
            result = quest.build_graph(session=session,message=message).invoke(message_invoke)
            
            
        """
        # ~ _logger.warning(f"chat {message=} {message.body=}")
        if (self.init_type == 'chat' and self.chat_user_id) or (self.init_type == "channel" and self.channel_id):
            if self._check_quest_error():
                raise UserError(self._check_quest_error())

            self.write({'real_channel_id': channel.id, 'real_chat_user_id': bot_user.id})
            session = message.parent_id.ai_quest_session_id if message.parent_id and message.parent_id.ai_quest_session_id else \
                message.parent_id.ai_quest_session_id if message.ai_quest_session_id else \
                    self.env['ai.quest.session'].quest_init(self)
            vals = self._chat_values(session=session, message=message, channel=channel, bot_user=bot_user)
            # ~ raise UserError(f"{vals=}")
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

    # ------------------------------------------------------------
    # Python code helpers
    # ------------------------------------------------------------

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

    def json2dict(self, text):
        text = text.split('```')[1].replace("json", "").replace("\n", "")
        return json.loads(text)

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
        message_body = html2plaintext(message.body) if message else ''
        eval_context = {
            'action': action,
            'env': self.env,
            'self': self,
            'session': kw.get('session', self.env['ai.quest.session'].quest_init(self)),
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
            'Warning': Warning,
            'UserError': UserError,
            # helpers
            '_logger': _logger,
            'html2plaintext': html2plaintext,
            'HumanMessage': HumanMessage,
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

        # objects = local_dict.get('objects', [])
        objects = {
            'ai_session_id': eval_context.get('session'),
            'ai_quest_id': eval_context.get('self'),
            'records': eval_context.get('records')
        }

        # if not eval_context.get('records'):
        #     objects.extend(eval_context.get('records'))
        # _logger.error(f"{local_dict=}")

        if local_dict.get('result'):
            messages = local_dict.get('result', {}).get('messages', [])
            result = self._get_last_ai_message(messages)
        elif local_dict.get('response'):
            messages = local_dict.get('response', {}).get('messages', [])
            result = self._get_last_ai_message(messages)
        else:
            return

        if not isinstance(result, list):
            result = [result]

        session.store_session_data(result=result, objects=objects)

        return local_dict

        #for department in records:
        #   _logger.warning(f{department})
        # ~ result = agent[0].prompt_agent(session=session,department=record.name)
        #                                                       #company_information=company_id.company_mission+company_id.company_values,
        #                                                       department=department.name,
        #                                                      quest_instructions=quest.description)
        #markdown.markdown(result)

        # res = False
        # action = self.sudo()
        # eval_context = self._get_eval_context(action, kwargs)
        # records = self.env.context.get('records')
        # if records:
        #     try:
        #         records.check_access_rule('write')
        #     except AccessError:
        #         _logger.warning(
        #             "Forbidden server action %r executed while the user %s does not have access to %s.",
        #             action.name, self.env.user.login, records,
        #         )
        #         raise
        #
        # _logger.warning(f"{eval_context=}")
        # run_self = action.with_context(eval_context['env'].context)
        # safe_eval(run_self.code.strip(), eval_context, mode="exec", nocopy=True, filename=str(self))
        # _logger.warning(f"{self.code=}  {eval_context=}")
        #
        # return eval_context.get('result', None)

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
                    {'name': quest.name, 'code': f"action = env.ref('{quest._get_eid()}').server_action(records)",
                     'binding_model_id': self.model_id.id if self.status == 'active' else None})
            if quest.cron_id:
                quest.cron_id.write({'name': quest.name, 'code': f"action = env.ref('{quest._get_eid()}').cron()"})
            if quest.channel_id:
                quest.channel_id.write({'name': quest.name, 'ai_quest_id': quest.id, })
            if quest.chat_user_id:
                quest.chat_user_id.write({'name': quest.name, 'login': quest.name, 'ai_quest_id': quest.id, })
        return result

    # ------------------------------------------------------------
    # LangGraph 
    # ------------------------------------------------------------

    # Inspired by https://github.com/menonpg/agentic_search_openai_langgraph/blob/main/agents.py
    def build_graph(self, **kwargs):
        """Build a multi-agent workflow graph."""

        if not self.ai_agent_ids:
            raise ValueError("No agents provided")

        agents = [line.ai_agent_id for line in self.ai_agent_ids]

        # Get member names
        members = [a.name for a in agents[1:]]
        _logger.info(f"Building graph with supervisor and {len(members)} workers: {members}")
        session = kwargs.get('session', False)

        if not session:
            raise UserError(_("No session added to build_graph method"))

        try:
            # Create graph
            graph_builder = StateGraph(AgentState)

            # Add supervisor
            supervisor = agents[0]
            _logger.info(f"Adding supervisor: {supervisor.name}")
            graph_builder.add_node("Supervisor", supervisor.create_supervisor(self, members, **kwargs))

            # Add worker nodes
            for agent in agents[1:]:
                _logger.info(f"Adding worker node: {agent.name}")
                graph_builder.add_node(agent.name, agent.create_node(session=session))

            # Add edges from workers to supervisor
            for member in members:
                _logger.info(f"Adding edge: {member} -> Supervisor")
                graph_builder.add_edge(member, "Supervisor")

            # Add conditional routing
            conditional_map = {k: k for k in members}
            conditional_map["FINISH"] = END

            _logger.info("Adding conditional edges with routes: " +
                         ", ".join([f"{k} -> {v}" for k, v in conditional_map.items()]))

            graph_builder.add_conditional_edges(
                "Supervisor",
                lambda x: x["next"],
                conditional_map
            )

            # Set entry point
            graph_builder.set_entry_point("Supervisor")

            # Compile and return
            _logger.info("Compiling graph")
            return graph_builder.compile()

        except Exception as e:
            self.log_message(f"Error building graph: {str(e)}", is_error=True)
            _logger.error(f"Error building graph: {str(e)}")
            raise

    # message = html2plaintext(message.body)
    # response = self.build_graph(agents).invoke({"messages": [HumanMessage(content=message)]})
    # result = response['messages'][-1].content
