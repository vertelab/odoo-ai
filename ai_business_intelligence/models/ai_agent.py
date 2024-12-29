from langchain.agents import AgentExecutor, create_openai_tools_agent
from langchain.prompts import PromptTemplate
from langchain_community.callbacks.manager import get_openai_callback
from langchain_community.callbacks.openai_info import OpenAICallbackHandler
from langchain_community.tools import DuckDuckGoSearchRun
from langchain_core.callbacks.base import BaseCallbackManager
from langchain_core.messages import AIMessage, HumanMessage, ChatMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph, MessagesState
from langgraph.prebuilt import ToolNode
from odoo import models, api, fields, _
from odoo.exceptions import UserError, AccessError, ValidationError
from typing import Annotated, Literal, TypedDict
import logging

_logger = logging.getLogger(__name__)


class CustomCallbackHandler(BaseCallbackManager):
    def __init__(self):
        self.total_tokens = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.successful_requests = 0
        self.total_cost = 0.0
        self.ignore_chain = False
        self.raise_error = False
        self.__enter__ = None
        self.ai_quest_id = None
        self.ignore_agent = False
        self.ignore_chat_model = False
        self.ignore_custom_event = False
        self.ignore_llm = False
        self.ignore_retriever = False

    # ~ def on_request(self, prompt, response):
        # ~ self.prompt_tokens += len(prompt.split())
        # ~ self.completion_tokens += len(response.split())
        # ~ self.total_tokens = self.prompt_tokens + self.completion_tokens
        # ~ self.successful_requests += 1
        # ~ _logger.warning(f"on_request: {prompt} {response}")
        
    def on_tool_start(self, serialized, input_str, *, run_id,  parent_run_id=None,**kwargs):
        _logger.warning(f"on_tool_start: {serialized=} {input_str=} {run_id=}  {parent_run_id=} {kwargs=} ")

    def on_tool_error(self, error, *, run_id, **kwargs):
        _logger.warning(f"on_tool_error: {error=} {run_id=} {kwargs=}")

    def on_tool_end(self, output, *, run_id, **kwargs):
        _logger.warning(f"on_tool_end: {output=} {run_id=} {kwargs=}")

    def on_text(self, text, *, run_id, parent_run_id=None):
        _logger.warning(f"on_text: {text=} {run_id=} {parent_run_id=}")

    def on_retry(self, retry_state, *, run_id, **kwargs):
        _logger.warning(f"on_retry: {retry_state=} {run_id=} {kwargs=}")

    def on_retriever_start(self, serialized, query, *, run_id):
        _logger.warning(f"on_retriever_start: {serialized=} {query=} {run_id=}")

    def on_retriever_error(self, error, *, run_id, **kwargs):
        _logger.warning(f"on_retriever_error: {error=} {run_id=} {kwargs=}")

    def on_retriever_end(self, documents, *, run_id, **kwargs):
        _logger.warning(f"on_retriever_end: {documents=} {run_id=} {kwargs=}")

    def on_llm_start(self, serialized, prompts, *, run_id):
        _logger.warning(f"on_llm_start: {serialized=} {prompts=} {run_id=}")

    def on_llm_new_token(self, token, *, chunk=None, **kwargs):
        _logger.warning(f"on_llm_new_token: {token=} {chunk=} {kwargs=}")

    def on_llm_error(self, error, *, run_id, **kwargs):
        _logger.warning(f"on_llm_error: {error=} {run_id=} {kwargs=}")

    def on_llm_end(self, response, *, run_id, **kwargs):
        _logger.warning(f"on_llm_end: {response=} {run_id=} {kwargs=}")

    def on_custom_event(self, name, data, *, run_id, **kwargs):
        _logger.warning(f"on_custom_event: {name=} {data=} {run_id=} {kwargs=}")

    def on_chat_model_start(self, serialized, messages, *, run_id, **kwargs):
        _logger.warning(f"on_chat_model_start: {serialized=} {messages=} {run_id=} {kwargs=}")

    def on_chain_start(self, serialized, inputs, *, run_id, parent_run_id=None, tags=None, metadata=None, name=''):
        _logger.warning(f"on_chain_start: {serialized=} {inputs=} {run_id=} {parent_run_id=} {tags=} {metadata=} {name=}")

    def on_chain_error(self, error, *, inputs=None, run_id=None, parent_run_id=None, tags=None, metadata=None):
        _logger.warning(f"on_chain_error: {error=} {inputs=} {run_id=} {parent_run_id=} {tags=} {metadata=}")

    def on_chain_end(self, outputs, *, run_id, inputs=None,parent_run_id=None, tags=None ):
        _logger.warning(f"on_chain_end: {outputs=} {run_id=} {inputs=}")


    def on_agent_finish(self, finish, *, run_id, **kwargs):
        _logger.warning(f"on_agent_finish: {finish=} {run_id=} {kwargs=}")
        

    def on_agent_action(self, action, *, run_id, **kwargs):
        _logger.warning(f"on_agent_action: {action=} {run_id=} {kwargs=}")

    # ~ def on_chain_start(self, prompt, run_id=None, parent_run_id=None, tags=None, metadata=None, response=None):
    # ~ def on_chain_start(self, prompt, tags=None, metadata=None, response=None, run_id=None,parent_run_id=''):
        # ~ _logger.warning(f"on_chain_start: {prompt=} {response=} {run_id=} {parent_run_id=} {tags=} {metadata=} {response=}")
        
    # ~ def on_chain_end(self, prompt,run_id=None, parent_run_id=None, tags=None, metadata=None, response=None):
        
        # ~ for message in prompt:
            # ~ if isinstance(message, AIMessage ):
                # ~ _logger.warning(f"on_chain_end AIMessage.usage_metadata: {message.usage_metadata=} ")
                # ~ session.store_session_data(message.usage_metadata)
        # ~ session.status = 'done'
        # ~ session.enddate = fields.Datetime.now()

        
        
        # ~ on_chain_end: 
            # ~ prompt={'messages': [
            # ~ AIMessage(content='', 
            # ~ additional_kwargs={'tool_calls': [{'id': 'call_VN28omWWNDgTYpskBpUu4iMj', 'function': {'arguments': '{"query":"San Francisco weather celsius"}', 'name': 'search'}, 
                                            # ~ 'type': 'function'}],
                                             # ~ 'refusal': None}, 
                                             # ~ response_metadata={
                                               # ~ 'token_usage': {'completion_tokens': 18, 'prompt_tokens': 56, 'total_tokens': 74, 
                                               # ~ 'completion_tokens_details': {'accepted_prediction_tokens': 0, 'audio_tokens': 0, 'reasoning_tokens': 0, 'rejected_prediction_tokens': 0}, 
                                               # ~ 'prompt_tokens_details': {'audio_tokens': 0, 'cached_tokens': 0}}, 
                                               # ~ 'model_name': 'gpt-4o-2024-08-06', 
                                               # ~ 'system_fingerprint': 'fp_d28bcae782', 
                                               # ~ 'finish_reason': 'tool_calls', 'logprobs': None}, 
                                               # ~ id='run-0aa89602-8b26-4d73-87c0-6705c75c8a06-0', 
                                               # ~ tool_calls=[{'name': 'search', 'args': {'query': 'San Francisco weather celsius'}, 'id': 'call_VN28omWWNDgTYpskBpUu4iMj', 'type': 'tool_call'}], 
                                               # ~ usage_metadata={'input_tokens': 56, 'output_tokens': 18, 'total_tokens': 74, 
                                               # ~ 'input_token_details': {'audio': 0, 'cache_read': 0}, 'output_token_details': {'audio': 0, 'reasoning': 0}})
                                               # ~ ]
                                               # ~ } 
                                               
                                               # ~ response=None run_id=UUID('c7408107-28f0-43a4-8d05-ae56e86189a1') 
                                               
                                               # ~ parent_run_id=UUID('c9ca6157-c0c8-49bb-85b8-134e7fb3fd58') 
                                               # ~ tags=['graph:step:1'] metadata=None response=None 
            
                                               

        
        # ~ response_metadata={'token_usage': {'completion_tokens': 18, 'prompt_tokens': 56, 'total_tokens': 74, 'completion_tokens_details': {'accepted_prediction_tokens': 0, 'audio_tokens': 0, 'reasoning_tokens': 0, 'rejected_prediction_tokens': 0}, 'prompt_tokens_details': {'audio_tokens': 0, 'cached_tokens': 0}}, 'model_name': 'gpt-4o-2024-08-06', 'system_fingerprint': 'fp_d28bcae782', 'finish_reason': 'tool_calls', 'logprobs': None}, id='run-0aa89602-8b26-4d73-87c0-6705c75c8a06-0', tool_calls=[{'name': 'search', 'args': {'query': 'San Francisco weather celsius'}, 'id': 'call_VN28omWWNDgTYpskBpUu4iMj', 'type': 'tool_call'}], usage_metadata={'input_tokens': 56, 'output_tokens': 18, 'total_tokens': 74, 'input_token_details': {'audio': 0, 'cache_read': 0}, 'output_token_details': {'audio': 0, 'reasoning': 0}})]} response=None run_id=UUID('c7408107-28f0-43a4-8d05-ae56e86189a1') parent_run_id=UUID('c9ca6157-c0c8-49bb-85b8-134e7fb3fd58') tags=['graph:step:1'] metadata=None response=None 

        # ~ _logger.warning(f"on_chain_end: {prompt=} {response=} {run_id=} {parent_run_id=} {tags=} {metadata=} {response=}")
        
    # ~ def on_chain_error(self, prompt,run_id=None, parent_run_id=None, tags=None, metadata=None, response=None):
        # ~ _logger.warning(f"on_chain_error: {prompt=} {response=} {run_id=} {parent_run_id=} {tags=} {metadata=} {response=}")

class AIAgent(models.Model):
    _inherit = "ai.agent"

    ai_type = fields.Selection(selection_add=[('business-intelligence', 'Business Intelligence')], ondelete={'business-intelligence': 'cascade'})





    def test2(self):
            search_tool = DuckDuckGoSearchRun()
            tools = [search_tool]

            react_openai_tools = """
            Answer the following questions as best you can. 
            You have access to a number of tools, use them to get the answer to the question.

            Reply in the following format:

            Question: the input question you must answer
            Thought: you should always think about what to do. Is the information so far sufficient, 
                or are more tool calls needed? ALWAYS start with a thought, NEVER just reply with a tool call.
            Action: the action to take, should be calling one of the tools
            Tool output: the result of the tool call
            ... (this Thought/Action/Tool output can repeat N times)
            Thought: I now know the final answer
            Final Answer: the final answer to the original input question

            Begin!

            Question: {input}
            Thought:{agent_scratchpad}
            """

            #langgraph
            #https://www.perplexity.ai/search/sktiv-pythonkod-i-langchain-fo-8Eq4hRtjQRS9YVGvaexP_w  


            prompt = PromptTemplate.from_template(react_openai_tools)
            
            custom_callback = CustomCallbackHandler()

            agent_executor= self.ai_agent_llm_id.get_agent_executor(prompt,tools,
                                    temperature=self.ai_temperature,verbose=True,
                                    callbacks=[custom_callback])


            
            # ~ agent_executor = AgentExecutor(agent=agent, tools=tools, callbacks=[CustomCallbackHandler()])

            with get_openai_callback() as cb:
                response = agent_executor.invoke(
                    {
                        "input": """Write me a prompt that implements the ReAct agent within LCEL using the OpenAI tools agent 
                        as described at https://github.com/langchain-ai/langchain/blob/master/libs/langchain/langchain/agents/openai_tools/base.py
                        Use the tools at your disposal to browse the web if necessary.
                        """,
                    }
                )
        
        # Logga token-användning och kostnader
                _logger.warning(f"Totalt antal tokens: {custom_callback.total_tokens}")
                _logger.warning(f"Prompt tokens: {custom_callback.prompt_tokens}")
                _logger.warning(f"Completion tokens: {custom_callback.completion_tokens}")
                _logger.warning(f"Lyckade förfrågningar: {custom_callback.successful_requests}")
                _logger.warning(f"Total kostnad: ${custom_callback.total_cost:.4f}")



    
            # ~ with get_openai_callback() as cb:
                # ~ response = agent_executor.invoke(
                # ~ {
                    # ~ "input": """Write me a prompt that implements the ReAct agent within LCEL using the OpenAI tools agent 
                    # ~ as described at https://github.com/langchain-ai/langchain/blob/master/libs/langchain/langchain/agents/openai_tools/base.py
                    # ~ Use the tools at your disposal to browse the web if necessary.
                    # ~ """,
                # ~ })                
                # ~ _logger.warning(f"Totalt antal tokens: {cb.total_tokens}")
                # ~ _logger.warning(f"Prompt tokens: {cb.prompt_tokens}")
                # ~ _logger.warning(f"Completion tokens: {cb.completion_tokens}")
                # ~ _logger.warning(f"Lyckade förfrågningar: {cb.successful_requests}")
                # ~ _logger.warning(f"Total kostnad: ${cb.total_cost:.4f}")
    
            # ~ session.store_session_data(out)
                session.status = 'done'
                session.enddate = fields.Datetime.now()

            # ~ raise UserError("%s" % out)







    def test(self):
        session = self.env['ai.quest.session'].agent_init(self[0])
        callback_handler = CustomCallbackHandler()

        # Define the tools for the agent to use
        @tool
        def search(query: str):
            """Call to surf the web."""
            # This is a placeholder, but don't tell the LLM that...
            if "sf" in query.lower() or "san francisco" in query.lower():
                return "It's 60 degrees and foggy."
            return "It's 90 degrees and sunny."

        tools = [search]
        tool_node = ToolNode(tools)
        
        # ~ model_str = self.ai_agent_llm_id.get_llm(verbose=True,temperature=0.7)
        # ~ _logger.warning(f"{model_str}")
        model = eval(self.ai_agent_llm_id.get_llm(verbose=True,temperature=0.7)).bind_tools(tools)
        # Define the function that determines whether to continue or not
        def should_continue(state: MessagesState) -> Literal["tools", END]:
            messages = state['messages']
            last_message = messages[-1]
            # If the LLM makes a tool call, then we route to the "tools" node
            if last_message.tool_calls:
                return "tools"
            # Otherwise, we stop (reply to the user)
            return END


        # Define the function that calls the model
        def call_model(state: MessagesState):
            messages = state['messages']  
            _logger.warning(f"call_model {state=}")
            # ~ with callback_handler as cb:
            response = model.invoke(messages)
            # We return a list, because this will get added to the existing list
            return {"messages": [response],}
                    # ~ 'totalt_tokens': cb.total_tokens}


        # Define a new graph
        workflow = StateGraph(MessagesState)

        # Define the two nodes we will cycle between
        workflow.add_node("agent", call_model)
        workflow.add_node("tools", tool_node)

        # Set the entrypoint as `agent`
        # This means that this node is the first one called
        workflow.add_edge(START, "agent")

        # We now add a conditional edge
        workflow.add_conditional_edges(
            # First, we define the start node. We use `agent`.
            # This means these are the edges taken after the `agent` node is called.
            "agent",
            # Next, we pass in the function that will determine which node is called next.
            should_continue,
        )

        # We now add a normal edge from `tools` to `agent`.
        # This means that after `tools` is called, `agent` node is called next.
        workflow.add_edge("tools", 'agent')

        # Initialize memory to persist state between graph runs
        checkpointer = MemorySaver()

        # Finally, we compile it!
        # This compiles it into a LangChain Runnable,
        # meaning you can use it as you would any other runnable.
        # Note that we're (optionally) passing the memory when compiling the graph
        app = workflow.compile(checkpointer=checkpointer)

        # Use the Runnable
        final_state = app.invoke(
            {"messages": [HumanMessage(content="what is the weather in sf, answer in swedish and celsius")]},
            config={"configurable": {"thread_id": 42},
                    #"callbacks":[callback_handler]
                    }
        )
        _logger.info(f"{final_state['messages'][-1].content}")
        if self.debug == True:
            _logger.warning(f"{final_state=}")
        for message in final_state['messages']:
            if isinstance(message,AIMessage):
          # ~ 'model_name': 'gpt-4o-2024-08-06', 
          # ~ 'system_fingerprint': 'fp_e161c81bbd', 
          # ~ 'finish_reason': 'stop', 'logprobs': None}, 
          # ~ id='run-c3a803af-f425-45f3-b13b-12ab2e9fd7e4-0', 
                session.store_session_data(message)
                
                _logger.warning(f"final_stage: {message.id=} {message.usage_metadata=} ")
            
        
        # ~ _logger.warning(f"{final_state['totalt_tokens']}")

            

