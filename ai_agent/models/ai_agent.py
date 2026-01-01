import importlib
import logging
import re
import traceback
from datetime import datetime
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_community.chat_message_histories import ChatMessageHistory
from langchain_core.documents.base import Blob
from langchain.agents import create_agent
from langchain_classic.chains.summarize import load_summarize_chain
from langchain_text_splitters import CharacterTextSplitter
from langchain_core.documents import Document

from odoo import models, fields, api, _
from odoo.exceptions import UserError
from random import randint

# https://python.langchain.com/api_reference/langchain/agents.html

_logger = logging.getLogger(__name__)


class SafeDict(dict):
    def __missing__(self, key):
        return '{' + key + '}'


class AIAgent(models.Model):
    _name = 'ai.agent'
    _description = 'AI Agent'
    _inherit = ["mail.thread", "mail.activity.mixin"]

    ai_agent_data_ids = fields.One2many(comodel_name="ai.agent.data", inverse_name="agent_id")
    ai_agent_llm_id = fields.Many2one(comodel_name="ai.agent.llm", string="LLM", help="Choose Large Language Model",
                                      domain="[('status','=','confirmed'),('is_embedded', '=', False)]")
    ai_backstory = fields.Text(string="Backstory")
    ai_description = fields.Text()
    ai_goal = fields.Text(string="Goal")
    ai_memory_ids = fields.One2many(comodel_name='ai.agent.memory', inverse_name='ai_agent_id', string="", help="")
    ai_prompt_template = fields.Html(string="Prompt Template")
    ai_role = fields.Char(string="Role")
    ai_temperature = fields.Float(string='Temperature', default=0.7,
                                  help="Temperature controls the randomness and creativity of the model's output, "
                                       "<1.0 more predictable and consistent >1.0 more diverse and creative responses")
    has_temperature = fields.Boolean(string="Has Temperature", related='ai_agent_llm_id.has_temperature')
    ai_tool_ids = fields.One2many(comodel_name='ai.agent.tool', inverse_name='ai_agent_id', string="", help="")
    ai_type = fields.Selection(selection=[("default", "Default"), ('ai-programmer', 'AI Programmer')],
                               default="default", required=True)
    base_image_128 = fields.Image("Base Image", max_width=128, max_height=128,
                                  compute='_compute_base_image_128')
    color = fields.Integer(default=lambda self: randint(1, 11))
    company_id = fields.Many2one(comodel_name='res.company',string="Company",help="") # domain|context|ondelete="'set null', 'restrict', 'cascade'"|auto_join|delegate
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
        selection=[("draft", "Draft"), ("active", "Active"), ("done", "Done"), ("error", "Error")],
        default="draft")
        
    status_color = fields.Integer(compute="compute_status_color")

    @api.depends("status")
    def compute_status_color(self):
        for record in self:
            record.status_color = 0
            if record.status == "draft":
                record.status_color = 3  # Orange
            elif record.status == "active":
                record.status_color = 10  # Green
            elif record.status == "done":
                record.status_color = 3  # Orange
            elif record.status == "error":
                record.status_color = 1  # Red

    tag_ids = fields.Many2many(comodel_name='product.tag', string='Tags')





    @api.depends('image_128')
    def _compute_base_image_128(self):
        for record in self:
            record.base_image_128 = record.image_128 or record.ai_agent_llm_id.image_128

    def action_get_quests(self):
        if self.session_line_ids:
            ai_quest_ids = list(set(map(lambda session_line_id: session_line_id.ai_quest_id.id, self.session_line_ids)))
            ai_quest_ids = list(
                set(map(lambda ai_quest_session_id: ai_quest_session_id.ai_quest_id.id, ai_quest_session_ids)))
            action = {
                'name': 'AI Quests',
                'type': 'ir.actions.act_window',
                'res_model': 'ai.quest',
                'view_mode': 'kanban,list,form,calendar',
                'target': 'current',
                'domain': [("id", 'in', ai_quest_ids)]
            }
            return action
        raise UserError("No quests connected to agent...")

    def action_get_session_lines(self):
        action = {
            'name': 'Session Lines',
            'type': 'ir.actions.act_window',
            'res_model': 'ai.quest.session.line',
            'view_mode': 'list,form,calendar,pivot',
            'target': 'current',
            'domain': [("ai_agent_id", '=', self.id)],
        }
        return action

    def action_get_sessions(self):
        action = {
            'name': 'Sessions',
            'type': 'ir.actions.act_window',
            'res_model': 'ai.quest.session',
            'view_mode': 'list,form,calendar',
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

    def extra_context(self, quest):
        res = {}
        if quest.use_company_info:
            res['company_info'] = f'Company information: {self.env.user.company_id.company_mission=} {self.env.user.company_id.company_values=}'
        if quest.use_personal_info:
            res['user_info'] = f'User information: {self.env.user.name=} {self.env.user.function=} {self.env.user.city=}'
        if quest.use_time_context:
            now = datetime.now()
            res['time_context'] = f'Current date {now.strftime("%Y-%m-%d")} Current time {now.strftime("%H:%M:%S")} Week Number {now.isocalendar()[1]}\n'
        return res

    def _extra_context(self, quest):
        res = ''
        for key, data in self.extra_context(quest).items():
            res += data
        return res

    def _chat_history(self, quest):
        if not (quest.init_type in ['chat', 'channel'] and quest.use_chat_history):
            return False
        chat_history = ChatMessageHistory()
        question = ''
        for m in self.env['mail.message'].search([
            ('model', '=', 'discuss.channel'),
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

    # ------------------------------------------------------------
    # LangChain
    # ------------------------------------------------------------

    def invoke(self, messages, **kwargs):
        response = self.ai_agent_llm_id.invoke(messages, **kwargs)
        return response

    def get_test_wizard(self):
        action = self.env.ref("ai_agent.action_ai_agent_test_wizard").read()[0]
        action["context"] = {"default_ai_agent_id": self.id}
        return action

    def get_agent_name(self, i, **kwargs):
        if kwargs.get('mermaid'):
            name = "**" + re.sub(r'[()\[\]\{\}:]', ' ', self.name.replace('|', ' ')).strip() + "**" if self and self.name else ""
            tools = "<small>fa&colon;fa-tools " + re.sub(r'[()\[\]{}:]', ' ', ','.join(
                [t.ai_tool_id.name.replace('|', ' ') for t in self.ai_tool_ids])) + "</small>\n" if self.ai_tool_ids else ''
            memories = "<small>fa&colon;fa-book " + re.sub(r'[()\[\]{}:]', ' ', ','.join(
                [m.ai_memory_id.name.replace('|', ' ') for m in self.ai_memory_ids if m.ai_memory_id])) + "</small>\n" if self.ai_memory_ids else ''
            llm = "<small>fa&colon;fa-cog " + re.sub(r'[()\[\]{}:]', ' ',
                                                     self.ai_agent_llm_id.name) + "</small>" if self.ai_agent_llm_id and self.ai_agent_llm_id.name else ''
            return f"{name}\n{tools}{memories}{llm}"
        else:
            name = re.sub(r'[()\[\]\{\}:]', ' ', self.name).strip() if self and self.name else ""
            return f"{name}"

    def generate_summary(self,text):
        # Initialize the language model
        llm = self.ai_agent_llm_id.get_llm()
        
        text_splitter = CharacterTextSplitter()
        texts = text_splitter.split_text(text)
        
        # Create Document objects
        docs = [Document(page_content=t) for t in texts]
                
        # Load the summarization chain
        chain = load_summarize_chain(llm, chain_type="map_reduce",verbose=True)
        
        # Generate the summary
        summary = chain.invoke(docs)
        
        response={}
        whole_text = ""
        for page in summary["input_documents"]:
            whole_text += page.page_content + "\n\n" 
        response["messages"] = [AIMessage(content=whole_text)]
        return response
        

    def test(self):
        self.last_run = fields.Datetime.now()        
        session = self.env['ai.quest.session'].agent_init(self)
        try:
            response = self.invoke("What is 1+1, answer with a single digit")
        except Exception as e:
            session.add_message(f"Could not confirm agent: {str(e)}\n{traceback.format_exc()}")
            self.message_post(body=_(f"Could not confirm agent: {str(e)}"), message_type="notification")
            session.status = 'done'
            return False
        session.status = 'done'
        if isinstance(response, AIMessage):
            content = response.content.strip()
            if content == "2":
                self.message_post(body=_(f"Llm confirmed: 1+1={content}"), message_type="notification")
                self.status = "active"
                return 
        self.message_post(body=_(f"Could not confirm agent: {response=}"), message_type="notification")


    def log_message(self, body, is_error=False):
        if is_error:
            self.status = "error"
        self.last_run = fields.Datetime.now()
        self.message_post(body=f"{body} | {self.last_run}", message_type="notification")
        self.env.cr.commit()

    def _extract_placeholders(self, template):
        # Regular expression to find {placeholder} and {{placeholder}}
        pattern = r'\{\{(.*?)\}\}|\{(.*?)\}'
        # Find all matches
        try:
            matches = re.findall(pattern, template)
        except Exception as e:
            _logger.error(f"_extract_placeholders {e=} {pattern=} {template=} {traceback.format_exc()}")
            return []
        # Extract only non-empty values from the matches
        placeholders = [match[0] or match[1] for match in matches]

        return placeholders

    def agent_extra_context(self, quest=None, record=None):
        if record:
            try:
                record_data = record.read()[0]

                # Extract placeholders from ai_prompt_template
                placeholders = self._extract_placeholders(self.ai_prompt_template or "")

                # Process the fields
                processed_data = {}
                for field, value in record_data.items():
                    # If the value is a tuple with an ID and name, extract the name
                    if isinstance(value, tuple) and len(value) == 2:
                        processed_data[field] = value[1]
                    else:
                        processed_data[field] = value  # Keep other field values as they are

                # Filter processed_data to only include fields in placeholders
                filtered_data = {key: processed_data[key] for key in placeholders if key in processed_data}
                return filtered_data
            except Exception as e:
                _logger.error(f"agent_extra_context {e=} {traceback.format_exc()}")
                return {}
        else:
            return {}

    def create_node(self, **kwargs):
        """Creates a node for the agent in the graph."""

        topic = kwargs.get('topic', kwargs.get('message', ''))
        session = kwargs.get('session', False)
        quest = session.ai_quest_id
        use_lang = f"Use language {self.env.user.lang}" if quest.use_personal_lang else ''
        quest_description = kwargs.get('quest_description', session.ai_quest_id.description)
        # ~ session.add_message(f"Agent {self.name} for {quest.name} create_node {kwargs=} ")

        def agent_node(state):
            """Process messages and generate a response."""

            # ~ if quest.debug:
            session.add_message(f"Agent {self.name} agent_node Initial state: ",state=state)

            messages = state.get('messages', [])
            if isinstance(messages, list) and hasattr(messages[-1], 'content'):
                latest_message = messages[-1].content
            else:
                latest_message = messages[-1]

            if isinstance(state.get('scratchpad', []), str):
                state['scratchpad'] = [state.get('scratchpad', '')]

            if quest.debug:
                # ~ session.add_message(f"Agent {self.name} received messages: {len(messages)} {messages=} {state=}")
                _logger.info(f"Agent {self.name} received messages: {len(messages)}  {state=}")
                
            system_message = SystemMessage(
                content=f"""You are an agent with specific responsibilities.
                Role: {self.ai_role}
                Goal: {self.ai_goal}
                Backstory: {self.ai_backstory}
                Memory: {self._get_memory(latest_message)} {self._get_memory(topic)}

                Instructions:
                - Provide thorough, complete responses
                - Use available tools and memory when needed
                - Stay focused on your specific role
                - {use_lang} 
                - {quest_description}
                
                Knowledge :
                    {self.agent_extra_context(quest=quest, record=kwargs.get('record'))}
                """
            )
            if quest.debug:
                session.add_message(f"Agent {self.name} system message: {system_message=} {state=}")
                _logger.error(f"Agent {self.name} system message: {system_message=} {session=} {state=}")

            messages = [system_message, HumanMessage(content=topic)]

            # Apply rate limiting before invoking LLM
            if not self.ai_agent_llm_id.check_rate_limits(input_text=topic):
                return False


            if quest.debug:
                # ~ self.log_message(f"Agent  {self.name} before invoke {messages=} {state=}")
                _logger.debug(f"Agent {self.name} {messages=} {state=}")

                # Get LLM
            llm = self.ai_agent_llm_id.get_llm()
            tools = self._get_tools(state)
            # ~ state['session'].add_message(f"Agent {self.name} {self.ai_agent_llm_id.name=} {llm=}  {tools=} {state=}")            
            try:
                if self.ai_agent_llm_id.is_asr:
                    file = ""
                    if not state.get("file"):
                        raise Exception(f"file in state is empty")
                    if type(state.get("file")) != type(Blob):
                        if Path(file).exists():
                            file = self.ai_agent_llm_id.make_blob(state.get("file"),is_path=True)
                        else:
                            file = self.ai_agent_llm_id.make_blob(state.get("file"))
                    llm = self.ai_agent_llm_id.get_transcription_llm()
                    transcriptions = llm.parse(file)
                    for t in transcriptions:
                        messages = [
                                    AIMessage(content=str(t.page_content),
                                    name=self.name.replace(' ', '_').replace(',', '').replace('.', '')) 
                                    for t in transcriptions]
                                    
                        result = {"messages": messages}
                        
                        
                else:
                    langgraph_agent_executor = create_agent(llm, tools=tools)

                    result = langgraph_agent_executor.invoke({
                        "input": latest_message,
                        "messages": messages
                    })

            except Exception as e:
                _logger.error(f"Error in agent {self.name}: {str(e)}")
                self.log_message(f"Agent {self.name} error: {str(e)}\n{traceback.format_exc()}")
                session.done_message(f"Agent {self.name} error: {str(e)}\n{traceback.format_exc()}")

                # return {
                #     "messages": [
                #         AIMessage(
                #             content=f"Agent {self.name} error: {str(e)}\n{traceback.format_exc()}",
                #             name=self.name.replace(' ', '_').replace(',', '').replace('.', '')
                #         )
                #     ]
                # }

            _logger.info(f"Agent {self.name} generated response")
            state['session'].save_messages(result.get('messages', []))

            # Get the last AI message from the result
            ai_messages = [m for m in result.get('messages', []) if isinstance(m, AIMessage)]
            if ai_messages:
                return result
            else:
                # If no AI messages found, create one from the result
                state['session'].add_message(f"No AI Messages: {str(result)=}")

                return {
                    "messages": [
                        AIMessage(
                            content=str(result),
                            name=self.name.replace(' ', '_').replace(',', '').replace('.', '')
                        )
                    ]
                }

        return agent_node

    def _get_memory(self, question, k=3, **kwarg):
        rags = []
        for ai_quest_memory_id in self.ai_memory_ids:
            if ai_quest_memory_id.ai_memory_id.vector_type == "faiss":
                ai_memory_id = ai_quest_memory_id.ai_memory_id
                db = ai_memory_id.load_faiss()
                if db:
                    docs = db.similarity_search(question, k=k)
                    for doc in docs:
                        if doc and doc.page_content:
                           rags.append(doc.page_content)
        return "\n".join(rags)

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
                _logger.error(
                    f"Error: {ai_tool_id.tool=} not found in {ai_tool_id.tool_lib=}  {traceback.format_exc()}")
            except Exception as e:
                _logger.error(f"An error occurred: {e}  {traceback.format_exc()}")
            if TOOL:
                tools.append(TOOL)
        return tools
