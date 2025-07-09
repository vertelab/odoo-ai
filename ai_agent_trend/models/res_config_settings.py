from odoo import api, fields, models, tools, _
from odoo.exceptions import ValidationError, UserError
import logging

_logger = logging.getLogger(__name__)

class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    youtube_api_key = fields.Char(string="Youtube API Key", help="Keys are created at https://console.cloud.google.com", 
                                config_parameter='ai_agent_trend.youtube_api_key')
  
