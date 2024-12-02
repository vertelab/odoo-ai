# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

import logging

from odoo import api, fields, models, tools, SUPERUSER_ID
from odoo.exceptions import UserError
from odoo.tools.translate import _
from odoo.tools.misc import get_lang
from pydantic import Field, BaseModel
# from langchain_core.pydantic_v2 import Field
# from pydantic import BaseModel
from langchain_core.output_parsers import JsonOutputParser


_logger = logging.getLogger(__name__)


class CRMAnalysis(BaseModel):
    create_new_crm: bool = Field(description="Whether to create a new CRM lead or not")
    answer: str = Field(description="Explanation of the decision")


class MailAI(models.Model):
    _inherit = "mail.ai"
    _description = "Mail managed by AI"
    _order = "id desc"

    def ai_method(self):
        if self.mail_alias_id.alias_name == "e-avrop":
            message_ids = self.message_ids
            agent = self.env['ai.agent'].search([('type', '=', 'e-avrop')])[0]

            parser = JsonOutputParser(pydantic_object=CRMAnalysis)

            answer = agent.prompt_agent(
                partial_variables=parser.get_format_instructions(),
                email=message_ids.mapped('body')[0])
            raise UserError(f"{answer}")
        else:
            return super().ai_method()

    def get_mail_activity(self):
        message_ids = self.message_ids
        ai_answer = self.mail_channel_id.ai_agent_id.create_agent(
            email=message_ids.mapped('body')[0]
        )
        raise UserError(ai_answer)

    def create_lead(self):
        lead_vals = {
            'name': self.name,
            'email_from': self.email_from,
            'mail_ai_id': self.id,
            'type': 'lead'
        }
        lead_id = self.env['crm.lead'].create(lead_vals)
        self.write({'crm_lead_id': lead_id.id})

    def action_show_crm_lead(self):
        return {
            'name': _('Lead'),
            'res_model': 'crm.lead',
            'view_mode': 'form',
            'res_id': self.crm_lead_id.id,
            'target': 'self',
            'type': 'ir.actions.act_window',
        }
