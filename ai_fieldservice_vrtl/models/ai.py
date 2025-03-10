from odoo import models, fields, api, _
from odoo.exceptions import UserError, AccessError, ValidationError

import logging

_logger = logging.getLogger(__name__)


class AIQuestSession(models.Model):
    _inherit = 'ai.quest.session'

    ai_type = fields.Selection(selection_add=[('fieldservice-order', 'Chat with servicorder')], ondelete={'fieldservice-order': 'cascade'})

class AIAgent(models.Model):
    _inherit = "ai.agent"

    ai_type = fields.Selection(selection_add=[('fieldservice-order', 'Chat with serviceorder')], ondelete={'fieldservice-order': 'cascade'})

class AIQuest(models.Model):
    _inherit = "ai.quest"

    def extra_context(self, quest):
        res = super(AIQuest).extra_context(quest)
        if quest.ai_type == "fieldservice-order":
           service_order = self.env['fieldservice.order'].search([('ai_quest_id','=',self.id)],limit=1)
           if service_order:
              res['service_order_info'] = f"""
              Service Order Number is {service_order.name}
              Description for service order is {service_order.description}
              """
        return res


    ai_type = fields.Selection(selection_add=[('fieldservice-order', 'Chat with serviceorder')], ondelete={'fieldservice-order': 'cascade'})
