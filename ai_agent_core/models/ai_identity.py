# -*- coding: utf-8 -*-
"""
Agent Identity — SOUL.md (ID-001 to ID-008).

ai.identity bundles an agent's complete identity:
- SOUL: personality, style, values, boundaries
- USER_MODEL: persistent model of the user
- SKILLS: bound competencies
- SYSTEM_PROMPT: compiled from all components
"""

import logging

from odoo import models, fields, api

_logger = logging.getLogger(__name__)


class AIIdentity(models.Model):
    """An agent's complete identity — who they ARE, not just what they DO."""
    _name = 'ai.identity'
    _description = 'AI Agent Identity'
    _order = 'name asc'

    name = fields.Char('Agent Name', required=True)
    active = fields.Boolean(default=True)

    # ── SOUL: Who the agent IS ──
    personality = fields.Text(
        'Personality',
        default='Hjälpsam, professionell, rak kommunikation.',
        help='Character traits. Shown in system prompt.',
    )
    style = fields.Text(
        'Communication Style',
        default='Säg "du", korta svar, inga emojis om inte användaren använder dem först.',
        help='How the agent communicates.',
    )
    values = fields.Text(
        'Values',
        default='Korrekthet > snabbhet. Transparens alltid.',
        help='Guiding principles.',
    )
    boundaries = fields.Text(
        'Boundaries',
        default='Gör inga juridiska tolkningar. Flagga osäkerhet.',
        help='Explicit limits — what the agent must NOT do.',
    )

    # ── User model ──
    user_model_enabled = fields.Boolean('Enable User Model', default=True)
    user_model = fields.Text(
        'User Model',
        help='Prose description of the user context. '
             'Private to the quest owner. '
             'Updated via /learn command.',
    )

    # ── Skills ──
    skill_ids = fields.Many2many(
        'ai.skill', 'ai_identity_skill_rel',
        'identity_id', 'skill_id',
        string='Skills',
    )

    # ── Scope ──
    scope = fields.Selection([
        ('personal', 'Personal'),
        ('organization', 'Organization'),
        ('public', 'Public'),
    ], default='personal', required=True,
       help='Personal: one user. Organization: shared. Public: anyone.')

    # ── Templates ──
    is_template = fields.Boolean('Is Template', default=False)
    template_category = fields.Selection([
        ('companion', 'Personal Companion'),
        ('analyst', 'Analyst'),
        ('accountant', 'Accountant'),
        ('developer', 'Developer'),
        ('support', 'Support'),
    ])

    # ── Compiled system prompt ──
    system_prompt = fields.Text(
        'System Prompt',
        compute='_compute_system_prompt',
        store=False,
        help='Compiled from soul + user_model + skills. '
             'Used as the agent\'s system prompt.',
    )

    # ── Stats ──
    use_count = fields.Integer('Times Used', default=0)

    @api.depends('name', 'personality', 'style', 'values', 'boundaries',
                 'user_model_enabled', 'user_model', 'skill_ids')
    def _compute_system_prompt(self):
        for rec in self:
            parts = [
                f"Du är {rec.name}.",
                "",
                "## Personlighet",
                rec.personality or '',
                "",
                "## Kommunikationsstil",
                rec.style or '',
                "",
                "## Värderingar",
                rec.values or '',
                "",
                "## Gränser",
                rec.boundaries or '',
            ]

            if rec.user_model_enabled and rec.user_model:
                parts.extend([
                    "",
                    "## Om användaren",
                    rec.user_model,
                ])

            if rec.skill_ids:
                parts.append("")
                parts.append("## Tillgängliga skills")
                for skill in rec.skill_ids:
                    parts.append(f"- **{skill.name}**: {skill.description}")

            rec.system_prompt = '\n'.join(parts)

    def action_use(self):
        """Increment use count."""
        self.use_count += 1
