# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.


import logging


from odoo import api, fields, models, tools, _
from odoo.exceptions import ValidationError, UserError


_logger = logging.getLogger(__name__)


class ResUsers(models.Model):
    _inherit = 'res.users'

    ai_quest_id = fields.Many2one(comodel_name='ai.quest', string="Quest", help="")
    ai_quest_session_id = fields.Many2one(comodel_name='ai.quest.session', string="Session", help="")
