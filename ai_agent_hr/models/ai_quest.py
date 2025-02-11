from odoo import models, api, fields, _
from odoo.exceptions import UserError, AccessError, ValidationError
import logging

_logger = logging.getLogger(__name__)


class AIAgent(models.Model):
    _inherit = "ai.agent"

    ai_type = fields.Selection(selection_add=[('oos', 'OOS'), ('ai-staff', 'AI Staff')],
                               ondelete={'oos': 'cascade', 'ai-staff': 'cascade'})


class AIQuest(models.Model):
    _inherit = "ai.quest"

    department_id = fields.Many2one(comodel_name='hr.department', )
    ai_type = fields.Selection(selection_add=[('oos', 'OOS'), ('ai-staff', 'AI Staff')],
                               ondelete={'oos': 'cascade', 'ai-staff': 'cascade'})

    def _get_quest_list_department(self, department):
        return ' '.join([f"'name': {q.name}, 'description': {q.description}, 'init_type': {q.init_type}" for q in
                         self.env['ai.quest'].search([('department_id', '=', department.id)])])

    def _get_jobs(self, department):
        return ' '.join(set((job['name'], job['description']) for job in self.env['hr.job'].search_read(
            [('employee_ids', 'in', self.env['hr.employee'].search([('department_id', '=', department.id)]).ids)],
            ['name', 'description'])))


class AISession(models.Model):
    _inherit = "ai.quest.session"

    department_id = fields.Many2one(comodel_name='hr.department', related="ai_quest_id.department_id", store=True)
    ai_type = fields.Selection(selection_add=[('oos', 'OOS'), ('ai-staff', 'AI Staff')],
                               ondelete={'oos': 'cascade', 'ai-staff': 'cascade'})


class AISessionLine(models.Model):
    _inherit = "ai.quest.session.line"

    department_id = fields.Many2one(comodel_name='hr.department',
                                    related="ai_quest_session_id.ai_quest_id.department_id", store=True)
