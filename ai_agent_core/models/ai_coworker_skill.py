# -*- coding: utf-8 -*-
"""
ai.coworker.skill — per-quest skill copy (fork model).

Each quest gets its own copy of a shared skill.
The quest can independently improve its copy.
Improvements can be proposed back to the shared skill.
"""

import logging
from odoo import models, fields, api, _

_logger = logging.getLogger(__name__)


class AICoworkerSkill(models.Model):
    """A quest-specific copy of a shared skill.

    Forked from ai.skill when a quest starts using it.
    Quest can independently improve its copy without
    affecting other quests using the same shared skill.
    """
    _name = 'ai.coworker.skill'
    _description = 'Quest Skill Copy'
    _order = 'sequence asc'

    coworker_id = fields.Many2one("ai.coworker", required=True, ondelete='cascade',
                                string='Quest')
    source_skill_id = fields.Many2one('ai.skill', string='Source Skill',
                                       readonly=True,
                                       help='The shared skill this was copied from')
    sequence = fields.Integer(default=10)

    # ── Copied fields (can diverge from source) ──
    name = fields.Char(related='source_skill_id.name', readonly=True, store=True)
    description = fields.Text('Description')
    recipe_text = fields.Text('Recipe',
        help='Quest-specific recipe. May diverge from shared skill.')
    trigger_keywords = fields.Char('Trigger Keywords')

    # ── Quest-specific improvements ──
    success_cases = fields.Text('Success Cases',
        help='Quest-specific examples of correct behavior.')
    failure_cases = fields.Text('Failure Cases',
        help='Quest-specific examples of incorrect behavior.')
    improvement_guidance = fields.Text('Improvement Guidance')
    improvement_references = fields.Text('Improvement References')
    version = fields.Integer('Version', default=1)
    last_improved = fields.Datetime('Last Improved')

    # ── Divergence tracking ──
    diverged = fields.Boolean('Diverged from Source',
        compute='_compute_diverged', store=True,
        help='True if this copy differs from the shared skill')
    source_version = fields.Integer('Source Version at Fork',
        help='Shared skill version when this was forked')

    # ── Feedback to shared skill ──
    feedback_proposed = fields.Boolean('Improvement Proposed',
        help='Quest improvements have been proposed back to shared skill')
    feedback_accepted = fields.Boolean('Accepted by Admin',
        help='Admin accepted the proposed improvements into shared skill')
    feedback_notes = fields.Text('Feedback Notes',
        help='Why improvements were proposed/rejected')

    @api.depends('recipe_text', 'success_cases', 'failure_cases',
                 'improvement_guidance', 'improvement_references')
    def _compute_diverged(self):
        for r in self:
            r.diverged = (
                r.improvement_guidance or
                r.improvement_references or
                (r.recipe_text and r.recipe_text != r.source_skill_id.recipe_text) or
                (r.success_cases and r.success_cases != r.source_skill_id.success_cases) or
                (r.failure_cases and r.failure_cases != r.source_skill_id.failure_cases)
            )

    @api.model
    def fork_for_quest(self, quest, skill):
        """Create a quest-specific copy of a shared skill."""
        existing = self.search([
            ('coworker_id', '=', coworker.id),
            ('source_skill_id', '=', skill.id),
        ], limit=1)
        if existing:
            return existing

        return self.create({
            'coworker_id': coworker.id,
            'source_skill_id': skill.id,
            'description': skill.description,
            'recipe_text': skill.recipe_text,
            'trigger_keywords': skill.trigger_keywords,
            'source_version': skill.version,
        })

    def action_improve(self):
        """Apply quest-specific improvement iteration."""
        self.ensure_one()
        self.version += 1
        self.last_improved = fields.Datetime.now()
        self.improvement_guidance = False
        self.improvement_references = False

    def action_propose_to_shared(self):
        """Propose quest improvements back to the shared skill."""
        self.ensure_one()
        if not self.source_skill_id:
            return

        # Copy improvements to shared skill
        updates = {}
        if self.improvement_guidance:
            updates['improvement_guidance'] = (
                (self.source_skill_id.improvement_guidance or '') +
                f'\n[Quest: {self.coworker_id.name}]\n{self.improvement_guidance}'
            )
        if self.success_cases:
            updates['success_cases'] = (
                (self.source_skill_id.success_cases or '') +
                f'\n[Quest: {self.coworker_id.name}]\n{self.success_cases}'
            )
        if updates:
            self.source_skill_id.write(updates)
            self.feedback_proposed = True
            self.feedback_notes = (
                f'Proposed improvements from quest "{self.coworker_id.name}" '
                f'on {fields.Datetime.now()}'
            )
            # Post message on shared skill
            self.source_skill_id.message_post(
                body=f'📝 **Improvement proposed** from quest "{self.coworker_id.name}"\n\n'
                     f'Version: {self.version}\n\n'
                     f'Guidance: {self.improvement_guidance or "None"}\n\n'
                     f'Cases: {self.success_cases or "None"}',
                message_type='comment',
                subtype_xmlid='mail.mt_comment',
            )
