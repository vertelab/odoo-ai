# -*- coding: utf-8 -*-
"""hr.department extension — AI-chefer och organisationsmål."""

from odoo import models, fields, api, _


class Department(models.Model):
    _inherit = 'hr.department'

    ai_manager_id = fields.Many2one(
        'ai.coworker', string='AI Chef',
        help='AI-medarbetare som är chef för denna avdelning.')

    department_objective_ids = fields.One2many(
        'ai.org.goal', 'department_id',
        string='Avdelningsmål',
        help='Mål kopplade till denna avdelning (ai.org.goal).')

    ai_coworker_ids = fields.One2many(
        'ai.coworker', 'department_id',
        string='AI-medarbetare',
        help='AI-medarbetare (ai.coworker) i denna avdelning.')

    total_ai_staff = fields.Integer(
        'Antal AI-medarbetare',
        compute='_compute_total_ai_staff',
        help='Antal AI-medarbetare i denna avdelning.')

    @api.depends('ai_coworker_ids')
    def _compute_total_ai_staff(self):
        for dept in self:
            dept.total_ai_staff = len(dept.ai_coworker_ids)

    @api.onchange('ai_manager_id')
    def _onchange_ai_manager(self):
        """När en AI sätts som avdelningschef, skapa hr.employee automatiskt."""
        if self.ai_manager_id:
            employee = self.ai_manager_id._ensure_employee()
            if employee:
                self.manager_id = employee.id
        elif not self.ai_manager_id:
            # Kolla om managern är en AI — isåfall rensa
            if self.manager_id and self.manager_id.is_ai:
                self.manager_id = False

    def action_get_ai_staff(self):
        """Öppna lista över AI-medarbetare i avdelningen."""
        self.ensure_one()
        return {
            'name': _('AI-medarbetare: %s') % self.name,
            'type': 'ir.actions.act_window',
            'res_model': 'ai.coworker',
            'view_mode': 'kanban,list,form',
            'domain': [('department_id', '=', self.id)],
            'target': 'current',
        }

    def action_open_ai_goals(self):
        """Öppna OKR (ai.org.goal) för avdelningen."""
        self.ensure_one()
        return {
            'name': _('Mål (OKR): %s') % self.name,
            'type': 'ir.actions.act_window',
            'res_model': 'ai.org.goal',
            'view_mode': 'kanban,list,form',
            'domain': [('department_id', '=', self.id)],
            'target': 'current',
        }

    def action_open_ai_tasks(self):
        """Öppna AI-tasks (ai.org.task) kopplade till avdelningen.

        Tasks knyts till avdelning via goal_id.department_id eller
        job_id.department_id (computed fält department_id på ai.org.task).
        """
        self.ensure_one()
        return {
            'name': _('AI Tasks: %s') % self.name,
            'type': 'ir.actions.act_window',
            'res_model': 'ai.org.task',
            'view_mode': 'list,form',
            'domain': [('department_id', '=', self.id)],
            'target': 'current',
        }

    # ════════════════════════════════════════════
    # Avdelningskontext + hälsa (tasks 7.5/7.6)
    # ════════════════════════════════════════════
    artifact_type_ids = fields.Many2many(
        'ai.artifact.type', string='Kontext (artifact types)',
        help='Avdelningens intressen — coworkerns company-scope-injektion '
             'filtreras på dessa (Produktion → mrp + stock; Försäljning → '
             'crm + sale.order). Ingen deklaration → allt injiceras.')

    health = fields.Selection([
        ('green', '🟢 Grön'),
        ('yellow', '🟡 Gul'),
        ('red', '🔴 Röd'),
    ], string='Hälsa', compute='_compute_health', store=False,
        help='Beräknad avdelningshälsa — aggregerad från coworkers '
             '(heartbeat, status, session_line_count, objective_ids). '
             'Gränsvärdena konfigureras i settings.')
    health_reason = fields.Text(
        string='Varför?', compute='_compute_health', store=False,
        help='Detaljerad förklaring till hälsostatus.')

    @api.depends('ai_coworker_ids.heartbeat_enabled',
                 'ai_coworker_ids.status',
                 'ai_coworker_ids.last_heartbeat',
                 'ai_coworker_ids.session_line_count',
                 'department_objective_ids')
    def _compute_health(self):
        """Beräkna avdelningshälsa (grön/gul/röd) från delmått.

        Trösklar läses från settings (icp) — inte hårdkodade:
        - health_heartbeat_stale_days (default 7)
        - health_active_ratio (default 0.5)
        - health_no_session_days (default 30)
        - health_zero_tokens_days (default 30)
        """
        icp = self.env['ir.config_parameter'].sudo()
        get = lambda k, d: int(icp.get_param('odoomind.okf.' + k, str(d)))
        stale_days = get('health_heartbeat_stale_days', 7)
        active_ratio = get('health_active_ratio', 50) / 100.0
        no_session_days = get('health_no_session_days', 30)
        zero_tokens_days = get('health_zero_tokens_days', 30)

        from datetime import timedelta
        now = fields.Datetime.now()
        stale_before = now - timedelta(days=stale_days)
        no_session_before = now - timedelta(days=no_session_days)
        zero_before = now - timedelta(days=zero_tokens_days)

        for dept in self:
            coworkers = dept.ai_coworker_ids
            if not coworkers:
                dept.health = 'red'
                dept.health_reason = 'Inga AI-medarbetare i avdelningen.'
                continue

            total = len(coworkers)
            heartbeating = sum(
                1 for c in coworkers
                if c.status == 'active' and c.heartbeat_enabled
                and c.last_heartbeat and c.last_heartbeat >= stale_before)
            active_count = sum(1 for c in coworkers if c.status == 'active')
            with_session = sum(
                1 for c in coworkers
                if c.session_line_ids and c.write_date and
                c.write_date >= no_session_before)
            with_tokens = sum(
                1 for c in coworkers
                if c.session_line_count and c.write_date and
                c.write_date >= zero_before)
            has_objective = bool(dept.department_objective_ids)

            reasons = []
            if heartbeating / total < active_ratio:
                reasons.append(
                    'Endast %d/%d heartbeatar (krav ≥ %d%%)'
                    % (heartbeating, total, int(active_ratio * 100)))
            if active_count == 0:
                reasons.append('Inga coworkers med status=active')
            if with_session == 0:
                reasons.append('Ingen session på %d dagar' % no_session_days)
            if with_tokens == 0:
                reasons.append('0 tokens på %d dagar' % zero_tokens_days)
            if not has_objective:
                reasons.append('Inga avdelningsmål (OKR)')

            if heartbeating / total >= active_ratio and active_count > 0 \
                    and with_session > 0 and with_tokens > 0 and has_objective:
                dept.health = 'green'
            elif active_count == 0 or with_tokens == 0:
                dept.health = 'red'
            else:
                dept.health = 'yellow'
            dept.health_reason = '; '.join(reasons) if reasons else 'Allt ser bra ut.'

    def _okf_context_domain(self):
        """Domän för avdelningens kontexturval (task 7.5).

        Returnerar artefakttyp-filter eller None (ingen deklaration → allt).
        """
        self.ensure_one()
        if self.artifact_type_ids:
            return [('artifact_type_id', 'in', self.artifact_type_ids.ids)]
        return None

    # ── Hierarkisk vy: fold/unfold + tasks/OKR (ai-orchestration-dashboard) ──
    fold = fields.Boolean('Fold (hierarki)',
        help='True = grenen är hopfälld i den hierarkiska vyn.')
    ai_task_count = fields.Integer(
        'AI Tasks', compute='_compute_ai_counts',
        help='Antal ai.org.task kopplade till avdelningen.')
    ai_goal_count = fields.Integer(
        'OKR', compute='_compute_ai_counts',
        help='Antal ai.org.goal kopplade till avdelningen.')

    @api.depends('department_objective_ids')
    def _compute_ai_counts(self):
        Task = self.env['ai.org.task']
        for dept in self:
            dept.ai_goal_count = len(dept.department_objective_ids)
            try:
                dept.ai_task_count = Task.search_count(
                    [('department_id', '=', dept.id)])
            except Exception:
                dept.ai_task_count = 0
    @api.model
    def org_chart_data(self):
        """Org-träd (avdelningar + AI-medarbetare/mål) för org_chart-vyn."""
        depts = self.search([])
        nodes = {}
        for d in depts:
            nodes[d.id] = {
                'id': d.id,
                'name': d.name,
                'parent_id': d.parent_id.id if d.parent_id else False,
                'color': d.color or 0,
                'manager_name': d.manager_id.name or '',
                'ai_manager_name': d.ai_manager_id.name or '',
                'health': d.health or '',
                'ai_staff': d.total_ai_staff,
                'ai_task_count': d.ai_task_count,
                'ai_goal_count': d.ai_goal_count,
                'goal_count': len(d.department_objective_ids),
                'child_ids': [],
            }
        roots = []
        for n in nodes.values():
            parent = n['parent_id']
            if parent and parent in nodes:
                nodes[parent]['child_ids'].append(n)
            else:
                roots.append(n)
        return {'roots': roots}
