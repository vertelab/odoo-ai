# -*- coding: utf-8 -*-
from odoo import models, fields


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    ai_context_default_enabled = fields.Boolean(
        string='Enable Record Context by Default',
        default=True,
        config_parameter='ai_agent_context.default_enabled',
        help="Automatically enable record context injection for new Quests."
    )
    ai_context_default_chatter = fields.Boolean(
        string='Include Chatter by Default',
        default=True,
        config_parameter='ai_agent_context.default_chatter',
        help="Automatically include chatter history in context for new Quests."
    )
    ai_context_chatter_limit = fields.Integer(
        string='Default Chatter Message Limit',
        default=20,
        config_parameter='ai_agent_context.chatter_limit',
        help="Default maximum number of chatter messages to include."
    )
