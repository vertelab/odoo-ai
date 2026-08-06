# -*- coding: utf-8 -*-
"""Memory scopes for AI Medarbetare (agent-memory-governance).

De tre OKF-scopen som kan injiceras/läras: company, personal, coworker.
Refereras av ai.coworker.memory_scopes (M2M) och kopplingens block-fält.
"""

from odoo import models, fields


class AIMemoryScope(models.Model):
    _name = 'ai.memory.scope'
    _description = 'Memory Scope'
    _order = 'sequence, id'

    name = fields.Char('Name', required=True)
    code = fields.Char('Code', required=True, size=32,
                       help='company | personal | coworker')
    sequence = fields.Integer(default=10)
    description = fields.Char('Description')
