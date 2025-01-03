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
    _name = 'ai.quest'
    _inherit = ["ai.quest","mail.alias.mixin"]

    ai_type = fields.Selection(selection_add=[('e-avrop', 'E-avrop')], ondelete={'e-avrop': 'cascade'})
    user_id = fields.Char()

    def create(self,val_list):
        ai_quest = super(AIQuest,self).create(val_list)
        self._alias_get_creation_values()
        return ai_quest
    
    def mail(self,mail,session):
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


    def _alias_get_creation_values(self):
        values = super(AIQuest, self)._alias_get_creation_values()
        values['alias_model_id'] = self.env['ir.model']._get('ai.quest.session').id
        values['alias_name'] = "e-avrop"
        values['alias_defaults'] = defualts = literal_eval(self.alias_defaults or "{}")
        defaults['ai_type'] = 'e-avrop'
        defaults['ai_quest_id'] = self.id
        return values


