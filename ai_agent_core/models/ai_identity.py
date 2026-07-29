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
        ('ops', 'Operations'),
        ('sales', 'Sales'),
    ])

    # ── Persona (OpenWorker-inspired) ──
    family = fields.Selection([
        ('knowledge', 'Knowledge'),
        ('code', 'Code'),
        ('companion', 'Companion'),
    ], default='knowledge', string='Family',
       help='Knowledge: research, reports. Code: development, ops. '
            'Companion: personal assistant.')
    recommended_models = fields.Char('Recommended Models',
                                      help='Comma-separated provider:model pairs. '
                                           'e.g. "openai:gpt-4o, anthropic:claude-sonnet-4"')
    default_permission_mode = fields.Selection([
        ('discuss', 'Discuss (Read-only)'),
        ('plan', 'Plan (Explore → Approve)'),
        ('interactive', 'Interactive (Ask per write)'),
        ('auto', 'Auto (Full access)'),
        ('custom', 'Custom'),
    ], default='interactive', string='Default Permission Mode')
    tool_ids = fields.Many2many('ai.tool', 'ai_identity_tool_rel',
                                 'identity_id', 'tool_id',
                                 string='Bound Tools')
    persona_md = fields.Text('Persona Markdown',
                              help='Raw markdown for export/import. '
                                   'Uses agentskills.io-compatible YAML frontmatter format.')
    template_id = fields.Many2one('ai.identity', string='Source Template',
                                   help='The original template this identity was forked from')

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
                parts.append("(Aktivera en skill explicit med /skill-name. "
                             "Skills aktiveras också automatiskt när användarens "
                             "prompt matchar trigger-nyckelord.)")
                for skill in rec.skill_ids:
                    trigger_info = ""
                    if skill.trigger_keywords:
                        trigger_info = f" [triggers: {skill.trigger_keywords}]"
                    parts.append(f"- **{skill.name}**: {skill.description}{trigger_info}")

            rec.system_prompt = '\n'.join(parts)

    def action_use(self):
        """Increment use count."""
        self.use_count += 1

    def copy_for_user(self, user):
        """Create a personal copy of this identity for a specific user (Hole 3).
        
        The copy starts from the template but lives independently —
        changes to the original template do NOT affect existing copies.
        Same pattern as ai.coworker.skill (quest-specific fork of shared skill).
        """
        self.ensure_one()
        copy = self.copy({
            'name': f"{self.name} — {user.name}",
            'scope': 'personal',
            'is_template': False,
            'template_id': self.id if self.is_template else self.template_id.id,
        })
        _logger.info('Created identity copy %s for user %s from template %s',
                     copy.name, user.name, self.name)
        return copy

    def action_import_persona_md(self):
        """Import persona from YAML frontmatter + markdown.

        Parses the persona_md field and populates:
        - name, description (from YAML)
        - family, recommended_models, default_permission_mode
        - skills (via skill names)
        - personality, style, values, boundaries (from body)

        Format (agentskills.io compatible):
            ---
            name: Agent Name
            description: What it does
            family: knowledge
            recommended_models: openai:gpt-4o
            default_permission_mode: interactive
            skills: [general, analysis]
            ---
            Body text becomes personality + system prompt.
        """
        self.ensure_one()
        if not self.persona_md:
            return

        import re
        import yaml

        md = self.persona_md.strip()
        frontmatter = {}
        body = md

        # Parse YAML frontmatter
        if md.startswith('---'):
            end = md.find('\n---', 3)
            if end != -1:
                try:
                    frontmatter = yaml.safe_load(md[3:end])
                    if not isinstance(frontmatter, dict):
                        frontmatter = {}
                except Exception as e:
                    _logger.warning('Failed to parse persona frontmatter: %s', e)
                body = md[end + 4:].lstrip('\n')

        vals = {}

        # Name
        if frontmatter.get('name') and not self.name:
            vals['name'] = frontmatter['name']
        if frontmatter.get('description') and not self.description:
            vals['description'] = frontmatter['description']

        # Family
        family_map = {
            'knowledge': 'knowledge', 'research': 'knowledge',
            'code': 'code', 'developer': 'code', 'dev': 'code',
            'companion': 'companion', 'assistant': 'companion',
        }
        if frontmatter.get('family'):
            fam = str(frontmatter['family']).lower().strip()
            if fam in family_map:
                vals['family'] = family_map[fam]

        # Recommended models
        if frontmatter.get('recommended_models'):
            recs = frontmatter['recommended_models']
            if isinstance(recs, list):
                vals['recommended_models'] = ', '.join(recs)
            else:
                vals['recommended_models'] = str(recs)

        # Permission mode
        mode_map = {
            'discuss': 'discuss', 'interactive': 'interactive',
            'plan': 'plan', 'auto': 'auto', 'custom': 'custom',
        }
        if frontmatter.get('default_permission_mode'):
            mode = str(frontmatter['default_permission_mode']).lower().strip()
            if mode in mode_map:
                vals['default_permission_mode'] = mode_map[mode]

        # Parse body into personality
        if body and body.strip():
            lines = body.strip().split('\n')
            # First 500 chars → personality summary
            first_lines = '\n'.join(lines[:10])
            if first_lines:
                vals['personality'] = first_lines[:2000]
            # Remaining → full system prompt body
            if len(body) > 2000:
                vals['style'] = body[2000:4000] if len(body) > 2000 else ''

        # Bind skills by name
        if frontmatter.get('skills') or frontmatter.get('tool_ids'):
            skill_names = frontmatter.get('skills', frontmatter.get('tool_ids', []))
            if isinstance(skill_names, str):
                skill_names = [s.strip() for s in skill_names.split(',') if s.strip()]
            if skill_names:
                skills = self.env['ai.skill'].search([
                    ('name', 'in', skill_names),
                ])
                if skills:
                    vals['skill_ids'] = [(6, 0, skills.ids)]

        if vals:
            self.write(vals)
            _logger.info('Imported persona: %s', self.name)

    def action_export_persona_md(self):
        """Export this identity as agentskills.io-compatible markdown."""
        self.ensure_one()
        lines = ['---']
        lines.append(f'name: {self.name}')
        if self.description:
            lines.append(f'description: {self.description[:200]}')
        lines.append(f'family: {self.family}')
        if self.recommended_models:
            lines.append(f'recommended_models: [{self.recommended_models}]')
        lines.append(f'default_permission_mode: {self.default_permission_mode}')
        if self.template_category:
            lines.append(f'category: {self.template_category}')
        if self.skill_ids:
            skill_names = ', '.join(s.name for s in self.skill_ids)
            lines.append(f'skills: [{skill_names}]')
        lines.append('---')
        lines.append('')
        # Body
        if self.personality:
            lines.append(self.personality)
        lines.append('')
        if self.style:
            lines.append(self.style)
        lines.append('')
        lines.append('## Values')
        lines.append(self.values or 'Korrekthet > snabbhet. Transparens alltid.')
        lines.append('')
        lines.append('## Boundaries')
        lines.append(self.boundaries or 'Gör inga juridiska tolkningar. Flagga osäkerhet.')

        self.persona_md = '\n'.join(lines)
        return {
            'type': 'ir.actions.act_window',
            'name': f'Export {self.name}',
            'res_model': 'ai.identity',
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'current',
        }
