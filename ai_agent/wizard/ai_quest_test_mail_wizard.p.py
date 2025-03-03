import uuid, base64, email
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


class AIQuestTestMailWizard(models.TransientModel):
    _name = 'ai.quest.test.mail.wizard'
    _description = 'AI Agent'

    eml_file = fields.Binary(string="Upload Mail", required=True)
    ai_quest_id = fields.Many2one(comodel_name="ai.quest", required=True)

    def test_mail(self):
        mail_thread = self.env["mail.thread"]
        byte_mail = base64.b64decode(self.eml_file)
        mail = email.message_from_bytes(byte_mail)
        is_mail = self.env["mail.message"].search([("message_id", "=", mail['Message-ID'])])
        if is_mail:
            is_mail.unlink()
        thread_id = mail_thread.message_process(model="ai.quest",message=base64.b64decode(self.eml_file),save_original=True)



            

