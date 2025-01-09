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


class DefaultDict(dict):
    def __missing__(self, key):
        return f'{key}: missing'  # Return an empty string or any default value you prefer


class AIAgent(models.Model):
    _name = 'ai.agent'
    _description = 'AI Agent'
    _inherit = ["mail.thread", "mail.activity.mixin"]

    ai_agent_data_ids = fields.One2many(comodel_name="ai.agent.data", inverse_name="agent_id")
    ai_agent_llm_id = fields.Many2one(comodel_name="ai.agent.llm", string="LLM", help="Choose Large Language Model",
                                      domain="[('status','=','confirmed')]")
    ai_backstory = fields.Text(string="Backstory")
    ai_discription = fields.Text()
    ai_goal = fields.Text(string="Goal")
    ai_prompt_template = fields.Html(string="Prompt Template")
    ai_role = fields.Char(string="Role")
    ai_memory_ids = fields.One2many(comodel_name='ai.agent.memory', inverse_name='ai_agent_id', string="",help="")
    ai_tool_ids = fields.One2many(comodel_name='ai.agent.tool', inverse_name='ai_agent_id', string="",help="")
    
    
    ai_temperature = fields.Float(string='Temperature', default=0.7,
                                  help="Temperature controls the randomness and creativity of the model's output, "
                                       "<1.0 more predictable and consistent >1.0 more diverse and creative responses")
    ai_type = fields.Selection(selection=[("default", "Default"), ('ai-programmer', 'AI Programmer')],
                               default="default", required=True)
    color = fields.Integer(default=lambda self: randint(1, 11))
    debug = fields.Boolean(string='Debug')
    is_favorite = fields.Boolean()
    last_run = fields.Datetime()
    name = fields.Char(required=True)
    quest_count = fields.Integer(compute="compute_quest_count")
    quest_ids = fields.Many2many(comodel_name="ai.quest")
    session_count = fields.Integer(compute="compute_session_count")
    session_line_count = fields.Integer(compute="compute_session_line_count")
    session_line_ids = fields.One2many(comodel_name="ai.quest.session.line", inverse_name="ai_agent_id")
    status = fields.Selection(
        selection=[("draft", _("Draft")), ("active", _("Active")), ("done", _("Done")), ("error", _("Error"))],
        default="draft")
    tag_ids = fields.Many2many(comodel_name='product.tag', string='Tags')
    image_128 = fields.Image("Image", max_width=128, max_height=128)
    base_image_128 = fields.Image("Base Image", max_width=128, max_height=128, compute='_compute_base_image_128')

    @api.depends('image_128')
    def _compute_base_image_128(self):
        for record in self:
            record.base_image_128 = record.image_128 or record.ai_agent_llm_id.image_128

    def action_get_quests(self):
        action = {
            'name': 'AI Quests',
            'type': 'ir.actions.act_window',
            'res_model': 'ai.quest',
            'view_mode': 'kanban,tree,form,calendar',
            'target': 'current',
            'domain': [("session_line_ids.ai_agent_id", '=', self.id)]
        }
        return action

    def action_get_session_lines(self):
        action = {
            'name': 'Session Lines',
            'type': 'ir.actions.act_window',
            'res_model': 'ai.quest.session.line',
            'view_mode': 'tree,form,calendar,pivot',
            'target': 'current',
            'domain': [("ai_agent_id", '=', self.id)],
        }
        return action

    def action_get_sessions(self):
        action = {
            'name': 'Sessions',
            'type': 'ir.actions.act_window',
            'res_model': 'ai.quest.session',
            'view_mode': 'tree,form,calendar',
            'target': 'current',
            'domain': [("session_line_ids.ai_agent_id", '=', self.id)]
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
                set(record.session_line_ids.filtered(lambda x: x.ai_agent_id.id == record.id).mapped(
                    'ai_quest_session_id')))

    @api.depends("session_line_ids")
    def compute_quest_count(self):
        for record in self:
            record.quest_count = len(
                set(record.session_line_ids.filtered(lambda x: x.ai_agent_id.id == record.id).mapped('ai_quest_id')))

    
    
    
    
    
    
    def prompt_agent(self, test_prompt=False, parser=False, session=False,debug=False, **kwargs):
        if debug:
            _logger.error(f"{self=}{session=}{kwargs=}")
        self.last_run = fields.Datetime.now()
        if debug:
            _logger.error(f"{session.session=} {self.last_run=}")

        if not self.ai_agent_llm_id:
            if debug:
                self.log_message("No LLM")
            raise UserError("No LLM")

        response = False

        # Create system message with agent context
        system_message = SystemMessage(content=f"""
Role: {self.ai_role}
Goal: {self.ai_goal}
Backstory: {self.ai_backstory}

Context and Guidelines:
- Always maintain the specified role
- Focus on achieving the defined goal
- Use the backstory to inform your responses

Guidlines and instructions {session.ai_quest.description}
""")
        if debug:
            self.log_message(f"{system_message}")

        human = HumanMessage(content=self.ai_prompt_template.format_map(DefaultDict(kwargs)))
        if debug:
            self.log_message(f"{human}")        
        try: 
            if debug:           
                self.log_message(f"{system_message=}{self._create_ai_template_prompt(kwargs, test_prompt, parser)=}")
                _logger.error(f"{system_message=}{self._create_ai_template_prompt(kwargs, test_prompt, parser)=}")
            response = eval(self.ai_agent_llm_id.get_llm()).invoke([system_message,human])

            _logger.error(f"{response=}")
        except HTTPStatusError as e:
            self.ai_agent_llm_id.log_message(body=e, is_error=True)
            _logger.error(f"HTTPStatusError {e=}")
            self.ai_agent_llm_id.log_message(body=f"HTTPStatusError {e=}")
            self.status = self.ai_agent_llm_id.status = 'error'
            self.log_message(body=f"HTTPStatusError {e=}")

        except Exception as e:
            _logger.error(f"{e=}")
            self.ai_agent_llm_id.log_message(body=f" {e=}")
            self.log_message(body=f" {e=}")

        _logger.error(f"{response=}")
        self.ai_agent_llm_id.log_message(body="Success!!!")

        if response and session:
            session.ai_agent_llm_id = self.ai_agent_llm_id
            return response
        return None

    def _create_ai_template_prompt(self, kwargs, test_prompt=False, parser=False, ):
        template = PromptTemplate(
            template=test_prompt or self.ai_prompt_template,
            input_variables=kwargs.keys(),
            partial_variables={"format_instructions": parser.get_format_instructions() if parser else False}
        )
        message = template.format(**kwargs)
        return message

    def get_test_wizard(self):
        action = self.env.ref("ai_agent.action_ai_agent_test_wizard").read()[0]
        _logger.error(f"{action=}")
        action["context"] = {"default_ai_agent_id": self.id}
        return action

    def test(self):
        self.last_run = fields.Datetime.now()

    def log_message(self, body, is_error=False):
        if is_error:
            self.status = "error"
        self.last_run = fields.Datetime.now()
        self.message_post(body=f"{body} | {self.last_run}", message_type="notification")



    # ------------------------------------------------------------
    # LangGraph 
    # ------------------------------------------------------------
    
    def create_supervisor(self,quest=quest,members=members):
        members = quest.ai_agent_ids.with_context({'agent': agent}).filtered(lambda a: a.ai_agent_id != agent)

        members = get_members()
        system_prompt = (
            f"""As a supervisor, your role is to oversee a dialogue between these"
            " workers: {members}. Based on the user's request,"
            " determine which worker should take the next action. Each worker is responsible for"
            " executing a specific task and reporting back their findings and progress. Once all tasks are complete,"
            " indicate with 'FINISH'.
            " Role: {self.ai_role}
            " Goal: {self.ai_goal}
            " Backstory: {self.ai_backstory}
            "
            " Context and Guidelines:
            "    - Always maintain the specified role
            "    - Focus on achieving the defined goal
            "    - Use the backstory to inform your responses
            "
            "  Guidlines and instructions {quest.description}
            """
          )
        options = ["FINISH"] + members

        function_def = {
            "name": "route",
            "description": "Select the next role.",
            "parameters": {
                "title": "routeSchema",
                "type": "object",
                "properties": {"next": {"title": "Next", "anyOf": [{"enum": options}] }},
                "required": ["next"],
            },
          }

        prompt = ChatPromptTemplate.from_messages([
            ("system", system_prompt),
            MessagesPlaceholder(variable_name="messages"),
            ("system", "Given the conversation above, who should act next? Or should we FINISH? Select one of: {options}"),
          ]).partial(options=str(options), members=", ".join(members))

        supervisor_chain = (prompt | llm.bind_functions(functions=[function_def], function_call="route") | JsonOutputFunctionsParser())
        return supervisor_chain
        
    def create_node(self):
        
        def agent_node(state, agent, name):
            result = agent.invoke(state)
            return {"messages": [HumanMessage(content=result["output"], name=name)]}

        
        prompt = ChatPromptTemplate.from_messages([
            ("system", f"""You are a researcher
            " Role: {self.ai_role}
            " Goal: {self.ai_goal}
            " Backstory: {self.ai_backstory}
            "
            " Context and Guidelines:
            "    - Always maintain the specified role
            "    - Focus on achieving the defined goal
            "    - Use the backstory to inform your responses
            "
            """),
            MessagesPlaceholder(variable_name="messages"),
            MessagesPlaceholder(variable_name="agent_scratchpad"),
            ])
        agent = create_openai_tools_agent(llm, self._get_tools(), prompt)
        executor = AgentExecutor(agent=agent, tools=self._get_tools(), verbose=True)
        
        search_agent = executor(llm, self._get_tools(), "You are an researcher")
        search_node = functools.partial(agent_node, agent=search_agent, name=self.name)

        return search_node
        

    def _get_tools(self):
        return []
        
        
        
        
        
        
    
