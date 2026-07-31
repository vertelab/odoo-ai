# -*- coding: utf-8 -*-
"""ai.skill — agentskills.io-compatible skill model."""

import logging
from odoo import models, fields, api

_logger = logging.getLogger(__name__)


class AISkill(models.Model):
    _name = 'ai.skill'
    _description = 'AI Skill'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'category, name asc'

    name = fields.Char(required=True)
    active = fields.Boolean(default=True)
    description = fields.Text(required=True,
        help='Max 1024 chars per agentskills.io standard.')

    # Visual
    image_128 = fields.Image('Image', max_width=128, max_height=128)
    color = fields.Integer(default=lambda self: __import__('random').randint(1, 11))

    # Source
    github_url = fields.Char('GitHub URL',
        help='URL to the original skill repository on GitHub')
    source_type = fields.Selection([
        ('odoo', 'Created in Odoo'),
        ('github', 'Imported from GitHub'),
        ('pi', 'Pi Agent Skill'),
    ], default='odoo')

    def _compute_github_avatar(self):
        """Fetch GitHub user avatar when source_type is github."""
        for skill in self:
            if skill.source_type == 'github' and skill.github_url and not skill.image_128:
                import re, base64, urllib.request, ssl
                m = re.search(r'github\.com/([^/]+)', skill.github_url)
                if m:
                    user = m.group(1)
                    try:
                        ctx = ssl.create_default_context()
                        ctx.check_hostname = False
                        ctx.verify_mode = ssl.CERT_NONE
                        req = urllib.request.Request(
                            f'https://github.com/{user}.png',
                            headers={'User-Agent': 'Odoo'}
                        )
                        with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
                            skill.image_128 = base64.b64encode(resp.read())
                    except Exception:
                        pass

    # Trigger
    trigger_keywords = fields.Char('Trigger Keywords',
        help='Comma-separated keywords')

    # Recipe
    recipe_text = fields.Text('Recipe',
        help='The canonical procedure. Agent reads this when activated.')

    # Compatibility
    compatibility = fields.Selection([
        ('any', 'Any Agent'),
        ('odoo', 'Odoo Agent'),
        ('pi_python', 'Pi Python Agent'),
        ('pi_node', 'Pi Node.js Agent'),
    ], default='any')

    # Required agents
    requires_agent_ids = fields.Many2many('ai.agent', 'ai_skill_required_agent_rel',
        'skill_id', 'agent_id', string='Required Agents',
        help='Agents that must exist for this skill to function. '
             'Auto-created when skill is activated on a coworker.')

    # Verify
    success_cases = fields.Text('Success Cases')
    failure_cases = fields.Text('Failure Cases')

    # Category
    category = fields.Selection([
        ('accounting', 'Accounting'),
        ('development', 'Development'),
        ('infrastructure', 'Infrastructure'),
        ('analysis', 'Analysis'),
        ('communication', 'Communication'),
        ('research', 'Research'),
        ('general', 'General'),
    ], default='general')

    # Stats
    use_count = fields.Integer(default=0)
    version = fields.Integer(default=1)
    last_improved = fields.Datetime('Last Improved')
    agent_count = fields.Integer(compute='_compute_agent_count')

    # Improvement
    improvement_guidance = fields.Text('Improvement Guidance')
    improvement_references = fields.Text('Improvement References')

    @api.depends()
    def _compute_agent_count(self):
        for r in self:
            r.agent_count = self.env['ai.agent'].search_count([
                ('skill_ids', 'in', r.id)
            ])

    def action_improve(self):
        self.ensure_one()
        self.version += 1
        self.last_improved = fields.Datetime.now()
        self.improvement_guidance = False
        self.improvement_references = False

    def action_apply_kaizen_suggestion(self, suggested_recipe=None, notes=''):
        """Apply a kaizen-approved improvement to this skill (HITL).

        Called after a human approves a kaizen finding. Increments version
        and stores improvement history.

        Args:
            suggested_recipe: optional new recipe_text to apply
            notes: human notes about why the change was approved
        """
        self.ensure_one()
        if suggested_recipe:
            self.recipe_text = suggested_recipe
        self.version += 1
        self.last_improved = fields.Datetime.now()
        if notes:
            self.improvement_references = (
                (self.improvement_references or '') + f'\n[{fields.Datetime.now()}] {notes}'
            ).strip()
        self.improvement_guidance = False
        _logger.info('Skill %s improved to version %d', self.name, self.version)

    def action_use(self):
        self.use_count += 1

    def action_open_skill_builder(self):
        """Open Skill Builder chat for this skill."""
        self.ensure_one()
        builder = self.env['ai.coworker'].search(
            [('name', '=', 'Skill Builder')], limit=1)
        if not builder:
            return {'type': 'ir.actions.act_url', 'url': '/ai/chat', 'target': 'new'}
        url = f'/ai/chat?coworker_id={builder.id}'
        if self.id:
            url += f'&context_skill={self.id}'
        return {'type': 'ir.actions.act_url', 'url': url, 'target': 'new'}

    def action_export_skill_md(self):
        """Export skill to agentskills.io-compatible SKILL.md."""
        self.ensure_one()
        import re
        n = re.sub(r'[^a-z0-9-]', '', self.name.lower().replace(' ', '-'))[:64]
        d = (self.description or '')[:1024]
        md = f'''---
name: {n}
description: {d}
compatibility: {self.compatibility}
metadata:
  category: {self.category}
  trigger_keywords: {self.trigger_keywords or ''}
---

{self.recipe_text or ''}
'''
        return {
            'type': 'ir.actions.act_window',
            'name': f'Export {self.name}',
            'res_model': 'ai.skill.export.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_skill_md': md, 'default_skill_id': self.id},
        }


class AISkillExportWizard(models.TransientModel):
    _name = 'ai.skill.export.wizard'
    _description = 'Export Skill to SKILL.md'

    skill_id = fields.Many2one('ai.skill', readonly=True)
    skill_md = fields.Text('SKILL.md', readonly=True)

    def action_copy(self):
        """Copy to clipboard hint."""
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Copied',
                'message': 'SKILL.md content ready — use Ctrl+C to copy',
                'type': 'success',
            }
        }
