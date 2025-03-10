from odoo import models, fields, api, _
from odoo.exceptions import UserError, AccessError, ValidationError

import logging

_logger = logging.getLogger(__name__)


class AIQuestSession(models.Model):
    _inherit = 'ai.quest.session'

    ai_type = fields.Selection(selection_add=[('fieldservice-order', 'Chat with ticket')], ondelete={'fieldservice-order': 'cascade'})

class AIAgent(models.Model):
    _inherit = "ai.agent"

    ai_type = fields.Selection(selection_add=[('fieldservice-order', 'Chat with ticket')], ondelete={'fieldservice-order': 'cascade'})

class AIQuest(models.Model):
    _inherit = "ai.quest"

    ai_type = fields.Selection(selection_add=[('fieldservice-order', 'Chat with ticket')], ondelete={'fieldservice-order': 'cascade'})
