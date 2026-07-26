# -*- coding: utf-8 -*-
"""ai.tag — lightweight AI-specific tag model.

Replaces product.tag dependency in ai_agent_core.
"""

from odoo import models, fields


class AITag(models.Model):
    _name = 'ai.tag'
    _description = 'AI Tag'
    _order = 'name'

    name = fields.Char('Name', required=True)
    color = fields.Integer('Color', default=0)
