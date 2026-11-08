# -*- coding: utf-8 -*-
"""
AI Tool Capability — serialiseringsenhet för verktyg (ai-tool-access-capabilities).

En förmåga = namn + AI-beskrivning + medlemsverktyg. Separerad från access:
ai.tool.group_ids styr VEM som får använda; förmågan styr VAD LLM:en ser
(enum-läge = en Tool med operation-enum; namespace-läge = individuella tools
+ förmågans beskrivning i systemprompten).
"""

from odoo import models, fields


class AIToolCapability(models.Model):
    _name = 'ai.tool.capability'
    _description = 'AI Tool Capability'
    _order = 'name asc'

    name = fields.Char('Name', required=True)
    active = fields.Boolean(default=True)
    description = fields.Text(
        'AI Description', required=True,
        help=('The capability AI description following the template (purpose/when/when not/example/output/guardrail). Used as the combined description when the capability is serialised in enum or namespace mode.'),
    )
    member_ids = fields.Many2many(
        'ai.tool', 'ai_tool_capability_member_rel',
        'capability_id', 'tool_id', string='Member Tools',
        help='Tools included in the capability.',
    )

    _sql_constraints = [
        ('name_unique', 'unique(name)', 'Capability name must be unique!'),
    ]
