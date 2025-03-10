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


    def agent_extra_context(self, quest):
        res = super().agent_extra_context(quest=quest)
        if self.ai_type == "fieldservice-order":
            service_order = self.env['fieldservice.order'].search([('ai_quest_id', '=', quest.id)], limit=1)
            if service_order:
                res['Service Order Number'] = service_order.name
                res['Service Order Description'] = service_order.description
        return res

class AIQuest(models.Model):
    _inherit = "ai.quest"

    ai_type = fields.Selection(
        selection_add=[('fieldservice-order', 'Chat with serviceorder')], ondelete={'fieldservice-order': 'cascade'})
