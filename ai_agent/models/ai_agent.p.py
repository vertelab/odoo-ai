import functools
import importlib
import json
import logging
import re
import traceback

from datetime import datetime
from httpx import HTTPStatusError
from langchain.prompts import ChatPromptTemplate, MessagesPlaceholder, PromptTemplate, HumanMessagePromptTemplate, \
    SystemMessagePromptTemplate
from langchain.schema import AIMessage, HumanMessage, SystemMessage, BaseMessage
from langchain.tools import tool
from langchain_community.chat_message_histories import ChatMessageHistory
from langgraph.prebuilt import create_react_agent
from odoo import models, fields, api, _
from odoo.exceptions import UserError
from random import randint
from typing_extensions import TypedDict, List

_logger = logging.getLogger(__name__)


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
    ai_memory_ids = fields.One2many(comodel_name='ai.agent.memory', inverse_name='ai_agent_id', string="", help="")
    ai_prompt_template = fields.Html(string="Prompt Template")
    ai_role = fields.Char(string="Role")
    ai_temperature = fields.Float(string='Temperature', default=0.7,
                                  help="Temperature controls the randomness and creativity of the model's output, "
                                       "<1.0 more predictable and consistent >1.0 more diverse and creative responses")
    ai_tool_ids = fields.One2many(comodel_name='ai.agent.tool', inverse_name='ai_agent_id', string="", help="")
    ai_type = fields.Selection(selection=[("default", "Default"), ('ai-programmer', 'AI Programmer')],
                               default="default", required=True)
    base_image_128 = fields.Image("Base Image", max_width=128, max_height=128,
                                  compute='_compute_base_image_128')
    color = fields.Integer(default=lambda self: randint(1, 11))
    debug = fields.Boolean(string='Debug')
    image_128 = fields.Image("Image", max_width=128, max_height=128)
    is_favorite = fields.Boolean()
    last_run = fields.Datetime()
    name = fields.Char(required=True)
    object_id = fields.Reference(string='Object', selection=lambda m: [(model.model, model.name) for model in
                                                                       m.env['ir.model'].sudo().search([])])
    quest_count = fields.Integer(compute="compute_quest_count")
    quest_ids = fields.Many2many(comodel_name="ai.quest")
    session_count = fields.Integer(compute="compute_session_count")
    session_line_count = fields.Integer(compute="compute_session_line_count")
    session_line_ids = fields.One2many(comodel_name="ai.quest.session.line", inverse_name="ai_agent_id")
    status = fields.Selection(
        selection=[("draft", _("Draft")), ("active", _("Active")), ("done", _("Done")), ("error", _("Error"))],
        default="draft")
    tag_ids = fields.Many2many(comodel_name='product.tag', string='Tags')

    @api.depends('image_128')
    def _compute_base_image_128(self):
        for record in self:
            record.base_image_128 = record.image_128 or record.ai_agent_llm_id.image_128

    def action_get_quests(self):
        ai_quest_session_ids = self.env["ai.quest.session"].search([("ai_agent_id", "=", self.id)])
        ai_quest_ids = list(
            set(map(lambda ai_quest_session_id: ai_quest_session_id.ai_quest_id.id, ai_quest_session_ids)))
        action = {
            'name': 'AI Quests',
            'type': 'ir.actions.act_window',
            'res_model': 'ai.quest',
            'view_mode': 'kanban,tree,form,calendar',
            'target': 'current',
            'domain': [("id", 'in', ai_quest_ids)]
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

    def _extra_context(self, quest):
        extra_context = ''
        if quest.use_company_info:
            extra_context += f'Company information: {self.env.user.company_id.company_mission=} {self.env.user.company_id.company_values=}\n'
        if quest.use_company_info:
            extra_context += f'User information: {self.env.user.name=} {self.env.user.function=} {self.env.user.city=}\n'
        if quest.use_time_context:
            now = datetime.now()
            extra_context += f'Current date {now.strftime("%Y-%m-%d")} Current time {now.strftime("%H:%M:%S")} Week Number {now.isocalendar()[1]}\n'
        return extra_context

    def _chat_history(self, quest):
        if not (quest.init_type in ['chat', 'channel'] and quest.use_chat_history):
            return False
        chat_history = ChatMessageHistory()
        question = ''
        for m in self.env['mail.message'].search([
            ('model', '=', 'mail.channel'),
            ('res_id', '=', quest.real_channel_id.id)],
                limit=quest.chat_history_limit, order='create_date asc'):
            if m.author_id.id == quest.real_chat_user_id.id:
                # This is an AI message
                if question:
                    # Add the previous user message if exists
                    chat_history.add_user_message(question)
                    question = ""
                chat_history.add_ai_message(m.body)
            else:
                # This is a user message
                if question:
                    question += "\n" + m.body
                else:
                    question = m.body
        # Add the last user message if exists
        if question:
            chat_history.add_user_message(question)
        return chat_history.messages

    def prompt_agent(self, test_prompt=False, parser=False, session=False, debug=False, channel=False, bot_user=False,
                     **kwargs):
        """
          Single agent prompting from quest.code
         
          result = agents[0].prompt_agent(
                   session=session,
                   debug=quest.debug,
                   message=html2plaintext(message.body),

        """
        self.last_run = fields.Datetime.now()
        if session:
            quest = session.ai_quest_id
        else:
            quest = self.env.ref('ai_agent.ai_quest_test')
        if debug:
            _logger.error(f"{self=}{session=} {quest=} {self.last_run} {kwargs=}")

        if not self.ai_agent_llm_id:
            if debug:
                self.log_message("No LLM")
            raise UserError("No LLM")

        response = False

        system_message_prompt = SystemMessagePromptTemplate.from_template("""
        Role: {role}
        Goal: {goal}
        Backstory: {backstory}
        {extra_context}
      
        Context and Guidelines:
        - Always maintain the specified role
        - Focus on achieving the defined goal
        - Use the backstory to inform your responses

        Guidelines and instructions: {instructions}
        {use_lang}
        """)

        # Create human message prompt
        human_message_prompt = HumanMessagePromptTemplate.from_template(self.ai_prompt_template)

        # Combine into chat prompt
        chat_prompt = ChatPromptTemplate.from_messages([
            system_message_prompt,
            # MessagesPlaceholder(variable_name="chat_history"),
            human_message_prompt
        ])
        # Use the chat prompt
        formatted_prompt = chat_prompt.format_prompt(
            role=self.ai_role,
            goal=self.ai_goal,
            backstory=self.ai_backstory,
            instructions=quest.description,
            extra_context=self._extra_context(quest),
            use_lang=f"Use language {self.env.user.lang}" if quest.use_personal_lang else '',
            # chat_history=self._chat_history(channel, bot_user,
            #                                 quest.chat_history_limit) if quest.use_chat_history else False,
            **kwargs
        )

        # If you need to log for debugging
        if debug:
            self.log_message(f"Formatted prompt: {formatted_prompt}")

        try:
            response = self.ai_agent_llm_id.get_llm().invoke(formatted_prompt)
            if debug:
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
            input_variables=list(kwargs.keys()) + ["chat_history"],
            partial_variables={"format_instructions": parser.get_format_instructions() if parser else False},
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

    def create_supervisor(self, quest, members, **kwarg):
        """Create a supervisor node that coordinates between different agents."""
        use_lang = f"Use language {self.env.user.lang} for the answer to Human" if quest.use_personal_lang else ''
        memory = self._get_memory(kwarg.get('message', ''))

        session = kwarg.get('session', False)

        system_prompt = f"""You are a supervisor coordinating between workers: {members}.
        Based on the request, determine which worker should handle the next step.
        Only choose FINISH when a complete response has been provided.

        Role: {self.ai_role}
        Goal: {self.ai_goal}
        Backstory: {self.ai_backstory}
        Guidelines: {quest.description}
        {self._extra_context(quest)}
        Message history: {self._chat_history(quest)}
       
        Memory: {memory}

        Instructions:
        1. Evaluate if we have a complete response
        2. If not complete, choose the most appropriate worker
        3. Send FINISH only when we have a satisfactory response
        4. Do not mention that you have done tool calls, thats too technical 
        4. {use_lang}
        """

        def supervisor_chain(state):
            messages = state.get('messages', [])
            state['session'] = session

            _logger.info(f"Supervisor received messages: {len(messages)} {session=}")
           
            if not messages:
                _logger.info(f"No messages, starting with first worker: {members[0] if members else 'no members'}")
                session.add_message(
                    f"No messages, starting with first worker: {members[0] if members else 'no members'}")
                return {"next": members[0], 'session': session} if members else {"next": "FINISH", 'session': session}

            _logger.error(f"{messages=} {session=}")

            # Get the latest message
            question = messages[-1].content if messages else ""

            try:
                # Create full message list
                _logger.error(f"Create full message list")
                prompt = f"Previous conversation:\n"
                for msg in messages:
                    prompt += f"\n{msg.content}\n"
                prompt += (f"\nBased on this, who should act next? Choose from: {members} or say FINISH if we have a "
                           f"complete response.")

                # Get LLM response
                llm = self.ai_agent_llm_id.get_llm()
                response = llm.invoke([
                    SystemMessage(content=system_prompt),
                    HumanMessage(content=prompt)
                ])

                # Parse response
                content = response.content.upper()
                _logger.info(f"Supervisor decision: {content}")
                session.add_message(f"Supervisor decision: {content}")

                # Check for completion or next agent
                if "FINISH" in content:
                    _logger.info("Supervisor decided to FINISH")
                    session.add_message("Supervisor decided to FINISH")
                    return {"next": "FINISH", 'session': session}

                # Find mentioned agent
                for member in members:
                    if member.upper() in content:
                        _logger.info(f"Supervisor selected agent: {member}")
                        session.add_message(f"Supervisor selected agent: {member}")
                        return {"next": member, 'session': session}

                # If no clear direction and we have previous responses, finish
                if len(messages) > 1:
                    _logger.info("No clear direction, finishing")
                    session.add_message("No clear direction, finishing")
                    return {"next": "FINISH", 'session': session}

                # Default to first member
                if len(members) != 0:
                    _logger.info(f"Defaulting to first member: {members[0]}")
                    session.add_message(f"Defaulting to first member: {members[0]}")
                    return {"next": members[0], 'session': session}

            except Exception as e:
                _logger.error(f"Error in supervisor chain: {str(e)}", exc_info=True)
                session.add_message(f"Error in supervisor chain: {str(e)}")
                return {"next": "FINISH", 'session': session}

        return supervisor_chain

    def create_node(self, **kwarg):
        """Creates a node for the agent in the graph."""

        def agent_node(state):
            """Process messages and generate a response."""
            messages = state.get('messages', [])
            _logger.info(f"Agent {self.name} received messages: {len(messages)} {state=}")
            state['session'].add_message(f"Agent {self.name} received messages: {len(messages)} {state=}")

            try:
                # Get the latest message
                latest_message = messages[-1].content if messages else ""

                system_message = SystemMessage(
                    content=f"""You are an agent with specific responsibilities.
                    Role: {self.ai_role}
                    Goal: {self.ai_goal}
                    Backstory: {self.ai_backstory}
                    Memory: {self._get_memory(latest_message)}
   
                    Instructions:
                    - Provide thorough, complete responses
                    - Use available tools and memory when needed
                    - Stay focused on your specific role
                    """
                )

                # Get LLM
                llm = self.ai_agent_llm_id.get_llm()
                tools = self._get_tools(state)

                langgraph_agent_executor = create_react_agent(llm, tools=tools)

                # Prepare the input messages with system message first
                input_messages = [system_message] + [messages[-1]]

                result = langgraph_agent_executor.invoke({
                    "input": latest_message,
                    "messages": input_messages
                })

                _logger.info(f"Agent {self.name} generated response")
                state['session'].save_messages(result.get('messages', []))
                # Return response
                # return result

                # Get the last AI message from the result
                ai_messages = [m for m in result.get('messages', []) if isinstance(m, AIMessage)]
                if ai_messages:
                    return result
                else:
                    # If no AI messages found, create one from the result
                    state['session'].add_message(f"No AImessages: {str(result)=}")

                    return {
                        "messages": [
                            AIMessage(
                                content=str(result),
                                name=self.name.replace(' ', '_').replace(',', '').replace('.', '')
                            )
                        ]
                    }

            except Exception as e:
                _logger.error(f"Error in agent {self.name}: {str(e)}")
                return {
                    "messages": [
                        AIMessage(
                            content=f"Error occurred: {str(e)}",
                            name=self.name.replace(' ', '_').replace(',', '').replace('.', '')
                        )
                    ]
                }

        return agent_node

    def _get_memory(self, question, k=3, **kwarg):
        def get_rag(vs, question):
            return "\n".join([doc.page_content for doc in vs.similarity_search(question, k=k)])

        return '\n'.join([get_rag(m.ai_memory_id.load_faiss(), question) for m in self.ai_memory_ids])

    def _get_tools(self, state=None):
        """Get the available tools for this agent."""
        tools = []
        for ai_tool_id in self.ai_tool_ids.mapped('ai_tool_id'):
            TOOL = None
            try:
                module = importlib.import_module(ai_tool_id.tool_lib)
                TOOL = getattr(module, ai_tool_id.tool)(state)
            except ImportError as e:
                _logger.error(f"Error importing {ai_tool_id.tool_lib=}: {e} {traceback.format_exc()}")
            except AttributeError as e:
                _logger.error(f"Error: {ai_tool_id.tool=} not found in {ai_tool_id.tool_lib=}  {traceback.format_exc()}")
            except Exception as e:
                _logger.error(f"An error occurred: {e}  {traceback.format_exc()}")
            if TOOL:
                tools.append(TOOL)
        _logger.warning(f"_get_tools{tools=}")
        return tools
