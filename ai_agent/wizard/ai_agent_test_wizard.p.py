import uuid
from pydantic import BaseModel, Field
from langchain_core.output_parsers import JsonOutputParser

from odoo import models, fields, api, _
from odoo.exceptions import UserError, AccessError, ValidationError
from odoo.tools.safe_eval import safe_eval
import logging

_logger = logging.getLogger(__name__)

class jsonResponse(BaseModel):
        case_nummber : str = Field(description="The case nummber for this tender. If you can't find this return false")
        question_and_answer: str = Field(description="The question and answer part of the mail. If you can't find this return false")
        prerequisite_change: str = Field(description="The changes that has been made to the tender. If you can't find this return false")


class AIAgentTestWizard(models.TransientModel):
    _name = 'ai.agent.test.wizard'
    _description = 'AI Agent'

    ai_agent_id = fields.Many2one(comodel_name="ai.agent")
    is_rise_error = fields.Boolean(default=True)
    ai_input = fields.Text(default="""{"question": "what is the meaning of life the universe and everything?", "answer": 42}""")
    ai_prompt_template = fields.Html(default= \
    """
        {answer}<br>
        ==============<br>
        Below this text, you have a question, and above you have the answer. Return the answer to the question.<br>
        ==============<br>
        {question}<br>
    """.strip())

    def test(self):
        for record in self:
            session = self.create_fake_session()
            record.ai_agent_id.ai_prompt_template = record.ai_prompt_template
            parsed_variables = JsonOutputParser(pydantic_object=jsonResponse)
            if self.is_rise_error:    
                raise UserError(
                    f"{record.ai_agent_id.prompt_agent(prompt=record.ai_prompt_template, parser=parsed_variables, session=session, **eval(record.ai_input))}"
                )
            _logger.error(f"{record.ai_agent_id.prompt_agent(prompt=record.ai_prompt_template, parser=parsed_variables, session=session, **eval(record.ai_input))}")

    def create_fake_session(self):
        return self.env["ai.quest.session"].create({"session": str(uuid.uuid4())})
            