# -*- coding: utf-8 -*-
"""Odoo Mind Workspace — PARA containers per user.

PARA (Projects, Areas, Resources, Archives) with polymorphic references —
never copies. Company projections can be placed (is_projection) but never owned.
"""

import logging

from odoo import models, fields, api, _

_logger = logging.getLogger(__name__)

# Known Odoo models that can be referenced from PARA containers.
# Kept as a white-list fallback; the real selection is dynamic.
_DEFAULT_REF_MODELS = [
    ('ai.okf.concept', 'OKF Concept'),
    ('sale.order', 'Sale Order'),
    ('res.partner', 'Partner'),
    ('calendar.event', 'Meeting'),
    ('project.task', 'Task'),
    ('crm.lead', 'Lead'),
    ('account.move', 'Invoice'),
    ('dms.file', 'DMS File'),
    ('mail.message', 'Message'),
    ('joplin.note', 'Joplin Note'),
]


class WorkspaceParaContainer(models.Model):
    """A PARA container owned by one user.

    Kinds:
      project  — active work with a goal (optional objective_id)
      area     — ongoing responsibility, AI-suggested with HITL
      resource — reference material, auto from kind=knowledge
      archive  — stale/retention, auto
    """

    _name = 'workspace.para.container'
    _description = 'Workspace PARA Container'
    _order = 'sequence asc, name asc'
    _rec_name = 'name'

    user_id = fields.Many2one(
        'res.users', string='User', required=True,
        default=lambda self: self.env.user,
        help='The user who owns this container.')
    name = fields.Char('Name', required=True)
    sequence = fields.Integer('Sequence', default=10)
    parent_id = fields.Many2one(
        'workspace.para.container', string='Parent',
        ondelete='cascade', help='Nested containers (e.g. project sub-areas).')

    kind = fields.Selection([
        ('project', 'Project'),
        ('area', 'Area'),
        ('resource', 'Resource'),
        ('archive', 'Archive'),
    ], string='Kind', required=True, default='project')

    state = fields.Selection([
        ('suggested', 'Suggested (AI)'),
        ('active', 'Active'),
        ('archived', 'Archived'),
    ], string='State', default='active',
        help='suggested = AI-föreslagen (task 3.5), kräver HITL-godkännande '
             'innan den blir aktiv.')

    is_projection = fields.Boolean(
        'Projection', default=False,
        help='True when this container shows company-projection data '
             'that the user does NOT own.')

    objective_id = fields.Many2one(
        'ai.personal.goal', string='Objective',
        help='Personal goal this project is working toward (SMART).')

    # Live references (polymorphic) — the container never copies records.
    ref_ids = fields.One2many(
        'workspace.para.ref', 'container_id', string='References',
        help='Live Odoo records referenced by this container.')

    # Derived counters for the PARA view.
    ref_count = fields.Integer(
        'Reference Count', compute='_compute_ref_count')

    @api.depends('ref_ids')
    def _compute_ref_count(self):
        for rec in self:
            rec.ref_count = len(rec.ref_ids)

    # -- Security: users only see their own containers (projections readable) --
    @api.model
    def _search(self, args, offset=0, limit=None, order=None, count=False,
                access_rights_uid=None):
        """Enforce per-user visibility unless system user."""
        if not self.env.user.has_group('base.group_system'):
            args = args or []
            args = [('user_id', '=', self.env.user.id)] + args
        return super()._search(
            args, offset=offset, limit=limit, order=order, count=count,
            access_rights_uid=access_rights_uid)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get('user_id'):
                vals['user_id'] = self.env.user.id
        return super().create(vals_list)

    # ── AI-föreslagna Areas (task 3.5) ──

    @api.model
    def suggest_areas(self, user=None, max_suggestions=5):
        """Föreslå Areas med HITL-godkännande (task 3.5).

        Källa till förslag:
        - mål-kategorier (ai.personal.goal.category)
        - hr.department.artifact_type_ids
        - vanliga partners (res.partner med flest mail/aktiviteter)
        - återkommande uppgifter (mail.activity-summor)

        Skapar containrar med state='suggested' som användaren godkänner
        i PARA-vyn (action_accept_suggestion).
        """
        user = user or self.env.user
        candidates = []

        # 1. Mål-kategorier → Area-namn
        if 'ai.personal.goal' in self.env:
            for goal in self.env['ai.personal.goal'].search(
                    [('user_id', '=', user.id),
                     ('status', 'in', ('proposed', 'accepted', 'active'))]):
                if goal.category and goal.category not in candidates:
                    candidates.append(goal.category)

        # 2. hr.department.artifact_type_ids → avdelningens kunskapsområden
        if 'hr.department' in self.env:
            depts = self.env['hr.department'].search(
                [('artifact_type_ids', '!=', False)])
            for dept in depts:
                for atype in dept.artifact_type_ids[:3]:
                    name = atype.name.replace('_', ' ').title()
                    if name not in candidates:
                        candidates.append(name)

        # 3. Vanliga partners (mail-flöde) → partner Areas
        if 'res.partner' in self.env:
            partners = self.env['res.partner'].search([
                ('is_company', '=', False),
            ], order='id desc', limit=5)
            for p in partners:
                if p.name not in candidates:
                    candidates.append(p.name)

        created = self.env['workspace.para.container']
        for name in candidates[:max_suggestions]:
            existing = self.search([
                ('user_id', '=', user.id),
                ('kind', '=', 'area'),
                ('name', '=', name),
            ], limit=1)
            if not existing:
                created |= self.create({
                    'user_id': user.id,
                    'name': name,
                    'kind': 'area',
                    'state': 'suggested',
                    'sequence': 20,
                })
        return created

    def action_accept_suggestion(self):
        """HITL-godkännande av AI-föreslagen Area (task 3.5)."""
        for rec in self:
            rec.write({'state': 'active'})
        return True

    def action_reject_suggestion(self):
        """HITL-avvisande: tar bort förslaget (task 3.5)."""
        self.unlink()
        return True

    # ── Projektavslut → lessons learned (task 4.4) ──

    def action_close_project(self):
        """Stäng ett PARA-projekt och destillera "lessons learned".

        - Flyttar containern till Archive-kind.
        - Skapar knowledge-koncept med L2/L3 från projektets refs
          (sammanfattning byggs mekaniskt av ref-titlar).
        - Rader skrivs aldrig; nya koncept skapas (ADD-only).
        """
        Concept = self.env['ai.okf.concept']
        ArtifactType = self.env['ai.artifact.type']
        atype = ArtifactType.search([('name', '=', 'learning')], limit=1) or \
            ArtifactType.search([('kind', '=', 'knowledge')], limit=1)
        for rec in self:
            rec.write({'kind': 'archive', 'state': 'archived'})
            if not rec.ref_ids:
                continue
            # Sammanfatta refs till en kort lessons-learned-text
            parts = []
            for ref in rec.ref_ids:
                label = ref.object_ref and str(ref.object_ref) or \
                    (ref.model, ref.res_id)
                parts.append('- %s' % label)
            summary = (
                f"Lessons learned från projektet \"{rec.name}\":\n"
                + '\n'.join(parts))
            concept = Concept._okf_upsert(
                atype,
                'lessons:%s:%s' % (rec.user_id.id, rec.name.lower()),
                summary,
                title='Lessons learned: %s' % rec.name,
                owner_user_id=rec.user_id.id or self.env.uid,
                generated_by='project_close', status='stable')
            concept.distill_l2_l3(
                l2='Erfarenheter från %s sammanfattade från %d kopplade '
                   'referenser.' % (rec.name, len(rec.ref_ids)),
                l3='Lessons learned: %s' % rec.name,
                generated_by='project_close')
        return True


class WorkspaceParaRef(models.Model):
    """A polymorphic reference from a PARA container to an Odoo record.

    Lazy revalidation: dead refs (deleted target) are cleaned on view open.
    """

    _name = 'workspace.para.ref'
    _description = 'Workspace PARA Reference'
    _order = 'create_date desc'

    container_id = fields.Many2one(
        'workspace.para.container', string='Container',
        required=True, ondelete='cascade')

    model = fields.Char('Model', required=True,
                        help='Target model name, e.g. sale.order')
    res_id = fields.Integer('Record ID', required=True,
                            help='Target record id, ondelete handled lazily')

    # For reference-selection UI we keep a dynamic reference field too.
    object_ref = fields.Reference(
        selection='_selection_ref_models', string='Object')

    # For ai.okf.concept targets we also keep the O2M backlink so inbox
    # views can compute "in inbox" / para_ref_ids without SQL.
    concept_id = fields.Many2one(
        'ai.okf.concept', string='OKF Concept',
        ondelete='cascade',
        help='Set when the referenced record is an ai.okf.concept. '
             'Kept in sync by the placement methods.')

    def _selection_ref_models(self):
        """Dynamic model list: safe defaults + anything with a name field."""
        models = list(_DEFAULT_REF_MODELS)
        known = {m[0] for m in models}
        try:
            for model_name in self.env.registry.models:
                if model_name.startswith('__') or model_name in known:
                    continue
                model = self.env[model_name]
                if hasattr(model, 'display_name') or 'name' in model._fields:
                    models.append((model_name, model_name))
        except Exception:
            pass  # non-fatal; fall back to defaults
        return models

    # -- Lazy revalidation (R3): drop dead refs on view open --
    def revalidate(self):
        """Remove references whose target no longer exists."""
        dead = self.env['workspace.para.ref']
        for ref in self:
            try:
                target = self.env[ref.model].browse(ref.res_id)
                if not target.exists():
                    dead |= ref
            except KeyError:
                # Model removed from registry — drop the ref
                dead |= ref
        if dead:
            _logger.info('Workspace PARA: dropping %d dead refs', len(dead))
            dead.unlink()
        return dead
