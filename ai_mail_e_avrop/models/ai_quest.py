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
    _inherit = "ai.quest"

    ai_type = fields.Selection(selection_add=[('e-avrop', 'E-avrop')], ondelete={'e-avrop': 'cascade'})

    def _alias_get_creation_values(self):
        values = super(AIQuest, self)._alias_get_creation_values()
        if self.ai_type == "e-avrop":
            values['alias_defaults'] = defaults = literal_eval(self.alias_defaults or "{}")
            defaults['ai_type'] = 'e-avrop'
            defaults['ai_quest_id'] = self.id
        return values


