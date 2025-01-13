import logging
import logging
import re
from datetime import datetime
from random import randint


from httpx import HTTPStatusError
from langchain.agents import AgentExecutor, create_openai_tools_agent
from langchain.embeddings import OpenAIEmbeddings
from langchain.memory import ConversationTokenBufferMemory
from langchain.prompts import ChatPromptTemplate, MessagesPlaceholder, PromptTemplate
from langchain.prompts import HumanMessagePromptTemplate, SystemMessagePromptTemplate
from langchain.schema import AIMessage, HumanMessage, SystemMessage
from langchain.tools import tool

from langchain_openai import ChatOpenAI
from langchain_groq import ChatGroq
from langchain_mistralai import ChatMistralAI


from odoo.exceptions import UserError

from odoo import models, fields, api, _

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
    ai_memory_ids = fields.One2many(comodel_name='ai.agent.memory', inverse_name='ai_agent_id', string="", help="")
    ai_tool_ids = fields.One2many(comodel_name='ai.agent.tool', inverse_name='ai_agent_id', string="", help="")

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

    def _chat_history(self, channel, bot_user, limit=10):
        if not channel:
            raise UserError("missing channel")
        if not bot_user:
            raise UserError("missing bot_user")
        chat_history = ChatMessageHistory()
        question = ''

        for m in self.env['mail.message'].search([
            ('model', '=', 'mail.channel'),
            ('res_id', '=', channel.id)],
                limit=limit, order='create_date asc'):
            if m.author_id.id == bot_user.id:
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
                   channel=channel,
                   bot_user=bot_user,
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
            response = eval(self.ai_agent_llm_id.get_llm()).invoke(formatted_prompt)
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

    def create_supervisor(self, quest, members):
        """Create a supervisor node that coordinates between different agents."""
        system_prompt = f"""You are a supervisor coordinating between workers: {members}.
        Based on the request, determine which worker should handle the next step.
        Only choose FINISH when a complete response has been provided.

        Role: {self.ai_role}
        Goal: {self.ai_goal}
        Backstory: {self.ai_backstory}
        Guidelines: {quest.description}

        Instructions:
        1. Evaluate if we have a complete response
        2. If not complete, choose the most appropriate worker
        3. Send FINISH only when we have a satisfactory response
        """

        def supervisor_chain(state):
            messages = state.get('messages', [])
            _logger.info(f"Supervisor received messages: {len(messages)}")

            if not messages:
                _logger.info(f"No messages, starting with first worker: {members[0]}")
                return {"next": members[0]} if members else {"next": "FINISH"}

            try:
                # Create full message list
                prompt = f"Previous conversation:\n"
                for msg in messages:
                    prompt += f"\n{msg.content}\n"
                prompt += (f"\nBased on this, who should act next? Choose from: {members} or say FINISH if we have a "
                           f"complete response.")

                # Get LLM response
                llm = eval(self.ai_agent_llm_id.get_llm())
                response = llm.invoke([
                    SystemMessage(content=system_prompt),
                    HumanMessage(content=prompt)
                ])

                # Parse response
                content = response.content.upper()
                _logger.info(f"Supervisor decision: {content}")

                # Check for completion or next agent
                if "FINISH" in content:
                    _logger.info("Supervisor decided to FINISH")
                    return {"next": "FINISH"}

                # Find mentioned agent
                for member in members:
                    if member.upper() in content:
                        _logger.info(f"Supervisor selected agent: {member}")
                        return {"next": member}

                # If no clear direction and we have previous responses, finish
                if len(messages) > 1:
                    _logger.info("No clear direction, finishing")
                    return {"next": "FINISH"}

                # Default to first member
                _logger.info(f"Defaulting to first member: {members[0]}")
                return {"next": members[0]}

            except Exception as e:
                _logger.error(f"Error in supervisor chain: {str(e)}")
                return {"next": "FINISH"}

        return supervisor_chain

    def XXXcreate_node(self):
        """Creates a node for the agent in the graph."""

        def agent_node(state):
            """Process messages and generate a response."""
            messages = state.get('messages', [])
            _logger.info(f"Agent {self.name} received messages: {len(messages)}")

            try:
                # Get the latest message
                latest_message = messages[-1].content if messages else ""

                # Create system prompt
                system_prompt = f"""You are an agent with specific responsibilities.
                Role: {self.ai_role}
                Goal: {self.ai_goal}
                Backstory: {self.ai_backstory}

                Instructions:
                - Provide thorough, complete responses
                - Use available tools when needed
                - Stay focused on your specific role
                """

                # Create prompt template with required agent_scratchpad
                prompt = ChatPromptTemplate.from_messages([
                    ("system", system_prompt),
                    MessagesPlaceholder(variable_name="messages"),
                    MessagesPlaceholder(variable_name="agent_scratchpad"),
                ])

                # Get LLM
                llm = eval(self.ai_agent_llm_id.get_llm())
                tools = self._get_tools()

                # Create agent
                agent = create_openai_tools_agent(llm, tools, prompt)

                # Create executor with limits
                executor = AgentExecutor(
                    agent=agent,
                    tools=tools,
                    verbose=True,
                    max_iterations=2,  # Limit tool usage
                    handle_parsing_errors=True
                )

                # Execute agent
                result = executor.invoke({
                    "input": latest_message,
                    "messages": messages
                })

                _logger.info(f"Agent {self.name} generated response")

                # Return response
                return {
                    "messages": [
                        HumanMessage(
                            content=result["output"],
                            name=re.sub(r'[^a-zA-Z0-9_-]', '', self.name)
                        )
                    ]
                }

            except Exception as e:
                _logger.error(f"Error in agent {self.name}: {str(e)}")
                return {
                    "messages": [
                        HumanMessage(
                            content=f"Error occurred: {str(e)}",
                            name=self.name
                        )
                    ]
                }

        return agent_node

    def _get_tools(self):
        """Get the available tools for this agent."""

        @tool("internet_search_DDGO", return_direct=False)
        def internet_search_DDGO(query: str) -> str:
            """Searches the internet using DuckDuckGo."""

            from duckduckgo_search import DDGS
            with DDGS() as ddgs:
                results = [r for r in ddgs.text(query, max_results=5)]
            return results if results else "No results found."

        @tool("process_content", return_direct=False)
        def process_content(url: str) -> str:
            """Processes content from a webpage."""

            from bs4 import BeautifulSoup
            import requests

            response = requests.get(url)
            soup = BeautifulSoup(response.content, 'html.parser')
            return soup.get_text()

        return [internet_search_DDGO, process_content]
        
    def create_node(self):
        """Creates a node for the agent in the graph."""

        def agent_node(state):
            """Process messages and generate a response."""
            messages = state.get('messages', [])
            _logger.info(f"Agent {self.name} received messages: {len(messages)}")
            llm = eval(self.ai_agent_llm_id.get_llm())
            history = ConversationTokenBufferMemory(llm=llm, max_token_limit=1000)
            
            try:
                # Get the latest message
                latest_message = messages[-1].content if messages else ""

                # Perform RAG
                context = ''
                for memory in self.ai_memory_ids:
                    if memory.memory_type == 'faiss':
                        relevant_docs = memory.load_faiss().similarity_search(latest_message, k=3)
                        context = "\n".join([doc.page_content for doc in relevant_docs])

                # Create system prompt
                system_prompt = f"""You are an agent with specific responsibilities.
                Role: {self.ai_role}
                Goal: {self.ai_goal}
                Backstory: {self.ai_backstory}

                Instructions:
                - Provide thorough, complete responses
                - Use available tools when needed
                - Stay focused on your specific role
                - Use the following context if relevant: {context}
                """

                # Create prompt template with required agent_scratchpad
                prompt = ChatPromptTemplate.from_messages([
                    ("system", system_prompt),
                    MessagesPlaceholder(variable_name="history"),
                    MessagesPlaceholder(variable_name="messages"),
                    MessagesPlaceholder(variable_name="agent_scratchpad"),
                ])

                # Get LLM
    
                tools = self._get_tools()

                # Create agent
                agent = create_openai_tools_agent(llm, tools, prompt)

                
                # Create executor with limits
                executor = AgentExecutor(
                    agent=agent,
                    tools=tools,
                    verbose=True,
                    max_iterations=2,  # Limit tool usage
                    handle_parsing_errors=True,
                    memory=history
                )

                # Execute agent
                result = executor.invoke({
                    "input": latest_message,
                    "messages": messages,
                    "history": history.chat_memory.messages
                })

                _logger.info(f"Agent {self.name} generated response")

                # Update memory with the new messages
                history.chat_memory.add_message(HumanMessage(content=latest_message))
                history.chat_memory.add_message(AIMessage(content=result["output"]))

                # Preserve complete message history
                full_history = history.chat_memory.messages

                # Extract usage metadata
                usage_metadata = result.get("__run", {}).get("usage_metadata", {})

                # Return response
                return {
                    "messages": [
                        AIMessage(
                            content=result["output"],
                            additional_kwargs={
                                "name": re.sub(r'[^a-zA-Z0-9_-]', '', self.name),
                                "usage_metadata": usage_metadata
                            }
                        )
                    ],
                    "full_history": full_history
                }

            except Exception as e:
                _logger.error(f"Error in agent {self.name}: {str(e)}")
                return {
                    "messages": [
                        AIMessage(
                            content=f"Error occurred: {str(e)}",
                            additional_kwargs={"name": self.name}
                        )
                    ],
                    "full_history": history.chat_memory.messages
                }

        return agent_node


