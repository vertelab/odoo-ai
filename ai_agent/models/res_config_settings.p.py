from langchain_core.messages import AIMessage
from odoo import api, fields, models, tools, _
from odoo.exceptions import ValidationError, UserError
import logging

_logger = logging.getLogger(__name__)

class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'
    
    quests = fields.Integer(string=" Quests", compute="_compute_quests")
    
    @api.depends('company_id')
    def _compute_quests(self):
        quests = self.env["ai.quest"].get_xmlrpc_quests()
        for res_config_id in self:
            res_config_id.quests = quests
    
