# -*- coding: utf-8 -*-
"""AI Organization Onboarding — processen att starta en AI-organisation."""

import logging
from odoo import models, fields, api, _

_logger = logging.getLogger(__name__)


class AIOnboarding(models.Model):
    _name = 'ai.onboarding'
    _description = 'AI Organization Onboarding Session'
    _rec_name = 'display_name'
    _order = 'create_date desc'

    company_id = fields.Many2one('res.company',
        default=lambda self: self.env.company, required=True)

    state = fields.Selection([
        ('draft', 'Not Started'),
        ('scanning', 'Scanning Modules'),
        ('interviewing', 'Interviewing'),
        ('analyzing', 'Analyzing'),
        ('proposal', 'Proposal Ready'),
        ('adjusting', 'Adjusting Proposal'),
        ('creating', 'Creating Organization'),
        ('completed', 'Completed'),
        ('cancelled', 'Cancelled'),
    ], default='draft', required=True)

    # Vem intervjuas
    ceo_user_id = fields.Many2one('res.users', string='CEO', required=True)
    ceo_partner_id = fields.Many2one(related='ceo_user_id.partner_id')

    # Intervjun (länkad till den allmänna coworkerns chatt-session)
    coworker_id = fields.Many2one('ai.coworker',
        string='Interview Coworker',
        help='Den allmänna coworkern som genomför intervjun.')
    interview_session_id = fields.Many2one('ai.coworker.session',
        string='Interview Session')

    # Vad vi upptäckte
    detected_modules = fields.Json(
        default=dict,
        help='{"account": {"installed": true}, "crm": {...}}')
    website_summary = fields.Text(
        help='Summering av företagets webbplats-RAG.')

    # Vad CEO:n svarade
    answers = fields.Json(default=dict,
        help='Strukturerade svar på intervjufrågorna.')

    # Förslaget
    proposal_json = fields.Json(default=dict,
        help='Det fullständiga organisationsförslaget.')
    template_id = fields.Many2one('ai.org.template',
        string='Template Used')

    # Vad som skapades
    created_department_ids = fields.Many2many('hr.department',
        string='Created Departments')
    created_coworker_ids = fields.Many2many('ai.coworker',
        string='Created Coworkers')
    created_goal_ids = fields.Many2many('ai.org.goal',
        string='Created Goals')
    created_employee_ids = fields.Many2many('hr.employee',
        string='Created Virtual Employees')

    # Justeringar från CEO
    adjustments = fields.Json(default=dict)

    display_name = fields.Char(compute='_compute_display_name')

    @api.depends('ceo_user_id.name', 'state', 'create_date')
    def _compute_display_name(self):
        for r in self:
            ceo = r.ceo_user_id.name or '?'
            state = dict(r._fields['state'].selection).get(r.state, '?')
            r.display_name = f'Onboarding: {ceo} — {state}'

    # ── Fas 1: Scan ──

    @api.model
    def detect_modules(self):
        """Skanna installerade Odoo-moduler och mappa till affärsdomäner."""
        module_map = {
            'account': 'Ekonomi & Redovisning',
            'crm': 'Säljprocess — leads & pipeline',
            'sale': 'Order & Offerter',
            'project': 'Projekt & Uppgifter',
            'stock': 'Lagerhantering',
            'mrp': 'Tillverkning — stycklistor & produktion',
            'fleet': 'Fordon & Körjournal',
            'maintenance': 'Underhåll — utrustning & scheman',
            'hr': 'Personal — anställda, frånvaro, rekrytering',
            'marketing': 'Marknadsföring — kampanjer & leads',
            'helpdesk': 'Support — ärenden & SLA',
            'website': 'Webbplats',
            'mgmtsystem': 'Ledningssystem — avvikelser & risker',
            'purchase': 'Inköp',
            'point_of_sale': 'Kassa',
        }
        detected = {}
        for module, description in module_map.items():
            try:
                if module in self.env.registry._init_modules:
                    detected[module] = {
                        'installed': True,
                        'description': description,
                    }
                else:
                    # Check via ir.module.module
                    mod = self.env['ir.module.module'].search([
                        ('name', '=', module),
                        ('state', '=', 'installed'),
                    ], limit=1)
                    if mod:
                        detected[module] = {
                            'installed': True,
                            'description': description,
                        }
            except Exception:
                pass
        return detected

    @api.model
    def read_website_rag(self):
        """Läs företagets webbplats-RAG om den finns."""
        company = self.env.company
        if not company.website_rag_attachment_id:
            return ''
        try:
            rag = company.website_rag_attachment_id.sudo()
            content = rag.datas.decode('utf-8') if rag.datas else ''
            return content[:5000]  # Max 5000 tecken
        except Exception as e:
            _logger.warning('Failed to read website RAG: %s', e)
            return ''

    # ── Actions ──

    def action_start_interview(self):
        """Starta intervjun — öppna /ai/chat med den allmänna coworkern."""
        self.ensure_one()
        coworker = self.coworker_id or self.env['ai.coworker'].search([
            ('is_default', '=', True)], limit=1)
        if not coworker:
            raise models.ValidationError(
                _('No default coworker found. Install ai_agent_core first.'))
        self.write({
            'state': 'interviewing',
            'coworker_id': coworker.id,
        })
        # Returnera action som öppnar chatt
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'ai.coworker',
            'res_id': coworker.id,
            'view_mode': 'form',
            'target': 'current',
            'context': {
                'form_view_initial_mode': 'edit',
                'ai_start_onboarding': self.id,
            },
        }

    def action_restart(self):
        """Starta om onboarding — scanna om, börja om."""
        self.ensure_one()
        old_created = (self.created_department_ids
                       | self.created_coworker_ids
                       | self.created_goal_ids
                       | self.created_employee_ids)
        self.write({
            'state': 'draft',
            'detected_modules': {},
            'website_summary': False,
            'answers': {},
            'proposal_json': {},
            'adjustments': {},
            'interview_session_id': False,
        })
        _logger.info('Onboarding %s restarted', self.id)
        return True

    def action_cancel(self):
        self.write({'state': 'cancelled'})
