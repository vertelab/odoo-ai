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
        help=('Förmågans AI-beskrivning enligt mallen (syfte/när/när inte/'
              'exempel/output/guardrail). Används som samlad beskrivning när '
              'förmågan serialiseras i enum- eller namespace-läge.'),
    )
    member_ids = fields.Many2many(
        'ai.tool', 'ai_tool_capability_member_rel',
        'capability_id', 'tool_id', string='Member Tools',
        help='Verktyg som ingår i förmågan.',
    )

    _sql_constraints = [
        ('name_unique', 'unique(name)', 'Capability name must be unique!'),
    ]
