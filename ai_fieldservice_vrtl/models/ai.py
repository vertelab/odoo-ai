from odoo import models, fields, api, _
from odoo.exceptions import UserError, AccessError, ValidationError

import logging

_logger = logging.getLogger(__name__)


class AIQuestSession(models.Model):
    _inherit = 'ai.quest.session'

    ai_type = fields.Selection(
        selection_add=[('fieldservice-order', 'Chat with servicorder')],
        ondelete={'fieldservice-order': 'cascade'}
    )


class AIAgent(models.Model):
    _inherit = "ai.agent"

    ai_type = fields.Selection(
        selection_add=[('fieldservice-order', 'Chat with serviceorder')], ondelete={'fieldservice-order': 'cascade'})


    def agent_extra_context(self, quest, record=None):
        res = super().agent_extra_context(quest=quest, record=record)
        if self.ai_type == "fieldservice-order":
            service_order = self.env['fieldservice.order'].search([('ai_quest_id', '=', quest.id)], limit=1)
            if service_order:
                res['Service Title'] = service_order.name
                res['Reference number'] = service_order.order_number
                res['Service Order Description'] = service_order.description
                res['The manufacturer or brand of the product'] = service_order.brand
                res['The model name or number of the product'] = service_order.model
                res['The unique serial number of the product'] = service_order.serial_number
                res['The product number or part number'] = service_order.product_number
                res['Any specific marking or label on the product'] = service_order.marking
                res['The date when the product was purchased'] = service_order.purchase_date
                res['Work instructions'] = service_order.work_instructions
                res['Directions to location'] = service_order.location_instructions
        return res

class AIQuest(models.Model):
    _inherit = "ai.quest"

    ai_type = fields.Selection(
        selection_add=[('fieldservice-order', 'Chat with serviceorder')], ondelete={'fieldservice-order': 'cascade'})
