# -*- coding: utf-8 -*-
"""
Skill Model — reusable agent competencies (SHARE-001 to SHARE-008).

ai.skill defines what an agent CAN do, independent of who they ARE.
Skills follow the Agent Skills standard (agentskills.io).
"""

import logging

from odoo import models, fields, api

_logger = logging.getLogger(__name__)


class AISkill(models.Model):
    """A reusable skill — what an agent can do."""
    _name = 'ai.skill'
    _description = 'AI Skill'
    _order = 'category, name asc'

    name = fields.Char('Skill Name', required=True)
    active = fields.Boolean(default=True)
    description = fields.Text(
        'Description',
        required=True,
        help='Max 1024 chars per agentskills.io standard. '
             'Describes what the skill does and when to use it.',
    )

    # ── Trigger ──
    trigger_keywords = fields.Char(
        'Trigger Keywords',
        help='Comma-separated keywords that activate this skill. '
             'Example: "moms, deklaration, skatteverket"',
    )

    # ── Recipes ──
    recipe_text = fields.Text(
        'Recipe',
        help='The canonical procedure for this skill. '
             'Agent reads this when the skill is activated. '
             'Taskless pattern: recipe is the authoritative source.',
    )

    # ── Compatibility ──
    compatibility = fields.Selection([
        ('any', 'Any Agent'),
        ('odoo', 'Odoo Agent'),
        ('pi_python', 'Pi Python Agent'),
        ('pi_node', 'Pi Node.js Agent'),
    ], default='any',
       help='Which agent types can use this skill.')

    # ── Verify cases (Taskless pattern) ──
    success_cases = fields.Text(
        'Success Cases',
        help='Examples of correct behavior. One per line.',
    )
    failure_cases = fields.Text(
        'Failure Cases',
        help='Examples of incorrect behavior. One per line.',
    )

    # ── Category ──
    category = fields.Selection([
        ('accounting', 'Accounting'),
        ('development', 'Development'),
        ('infrastructure', 'Infrastructure'),
        ('analysis', 'Analysis'),
        ('communication', 'Communication'),
        ('general', 'General'),
    ], default='general')

    # ── Stats ──
    use_count = fields.Integer('Times Used', default=0)
    version = fields.Integer('Version', default=1)
    last_improved = fields.Datetime('Last Improved')

    # ── Improvement (Taskless IMPROVE pattern) ──
    improvement_guidance = fields.Text(
        'Improvement Guidance',
        help='Feedback for improving this skill. '
             'Taskless pattern: guidance + references → iterate.',
    )
    improvement_references = fields.Text(
        'Improvement References',
        help='Concrete examples for improvement. '
             'Taskless pattern: false positives/negatives.',
    )

    def action_improve(self):
        """Run one improvement iteration (Taskless IMPROVE)."""
        self.ensure_one()
        self.version += 1
        self.last_improved = fields.Datetime.now()
        # Clear guidance after applying
        self.improvement_guidance = False
        self.improvement_references = False

    def action_use(self):
        """Increment use count."""
        self.use_count += 1
