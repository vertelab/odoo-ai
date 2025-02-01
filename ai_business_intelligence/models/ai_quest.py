import json

from pydantic import BaseModel, Field
from langchain_core.output_parsers import JsonOutputParser
from ast import literal_eval

from odoo import models, fields, api, _
from odoo.exceptions import UserError, AccessError, ValidationError
import logging

_logger = logging.getLogger(__name__)

class jsonResponse(BaseModel):
    case_nummber : str = Field(description="The case nummber for this tender. If you can't find this return false")
    question_and_answer: str = Field(description="The question and answer part of the mail. If you can't find this return false")
    prerequisite_change: str = Field(description="The changes that has been made to the tender. If you can't find this return false")

class AIQuest(models.Model):
    _inherit = 'ai.quest'

    ai_type = fields.Selection(selection_add=[('business-intelligence', 'Business Intelligence')],ondelete={'business-intelligence': 'cascade'})
    user_id = fields.Char()

    def create(self,val_list):
        ai_quest = super(AIQuest,self).create(val_list)
        self._alias_get_creation_values()
        return ai_quest
    
    def run(self,mail,session):
        _logger.error(f"{session.session=}")
        parser = JsonOutputParser(pydantic_object=jsonResponse)
        if self.ai_type == "e-avrop":
            agent_id = self.env["ai.agent"].search([("ai_type", "=", "e-avrop")], limit=1)
            response = agent_id.prompt_agent(parser=parser, mail=mail,session=session)
            response = response.replace('json\n','').replace('```','')
            response = json.loads(response)
            if response.get("case_nummber"):
                lead = self.env["crm.lead"].create({"name": f"{mail.subject}[{response.get('case_nummber')}]", "email_from": mail.email_from})
                if response.get("question_and_answer") != False:
                    lead.message_post(body=f"{response.get('question_and_answer')}",message_type="notification")
                if response.get("prerequisite_change") != False:
                    lead.message_post(body=f"{response.get('prerequisite_change')}",message_type="notification")
                lead.message_post(body=f"{mail.body}",message_type="notification")


    def test(self):
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

            prompt = PromptTemplate.from_template(react_openai_tools)
            
            agent_executor= self.ai_agent_llm_id.get_agent_executor(prompt,tools,temperature=self.ai_temperature,verbose=True)

    
            session_ids =  self.env['ai.quest.session'].search([('ai_quest_id','=',self.id),('status','=','active')])
            if len(sesson_ids)>=1:
                session=session_ids[0]
            else:
                session = self.env['ai.quest.session'].create({'ai_quest_id': self.id,'status': 'active'})
    
    
            out = agent_executor.invoke(
                {
                    "input": """Write me a prompt that implements the ReAct agent within LCEL using the OpenAI tools agent 
                    as described at https://github.com/langchain-ai/langchain/blob/master/libs/langchain/langchain/agents/openai_tools/base.py
                    Use the tools at your disposal to browse the web if necessary.
                    """,
                }
            )
            session.store_session_data(out)
            session.status = 'done'
            session.enddate = fields.Datetime.now()

            # ~ raise UserError("%s" % out)
