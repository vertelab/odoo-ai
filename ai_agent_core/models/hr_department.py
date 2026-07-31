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
