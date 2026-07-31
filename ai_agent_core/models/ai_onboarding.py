# -*- coding: utf-8 -*-
"""AI Organization Onboarding — processen att starta en AI-organisation."""

import json
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

    # Intervjun (länkad till den allmänna coworkern)
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
    current_question = fields.Text('Current Question')
    question_index = fields.Integer('Question Index', default=0)
    total_questions = fields.Integer('Total Questions', default=0)

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
    created_task_ids = fields.Many2many('ai.org.task',
        string='Created Tasks')

    # Justeringar från CEO
    adjustments = fields.Json(default=dict)

    display_name = fields.Char(compute='_compute_display_name')

    @api.depends('ceo_user_id.name', 'state', 'create_date')
    def _compute_display_name(self):
        for r in self:
            ceo = r.ceo_user_id.name or '?'
            state = dict(r._fields['state'].selection).get(r.state, '?')
            r.display_name = f'Onboarding: {ceo} — {state}'

    # ════════════════════════════════════════════
    # FAS 1: SKANNA
    # ════════════════════════════════════════════

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
            return content[:5000]
        except Exception as e:
            _logger.warning('Failed to read website RAG: %s', e)
            return ''

    def action_scan(self):
        """Fas 1: Skanna moduler, RAG, och välj template."""
        self.ensure_one()
        modules = self.detect_modules()
        website = self.read_website_rag()
        self.write({
            'state': 'scanning',
            'detected_modules': modules,
            'website_summary': website,
        })
        # Välj template
        template = self._select_template(modules)
        if template:
            self.template_id = template.id
        return True

    def _select_template(self, modules):
        """Välj template baserat på installerade moduler."""
        installed = set(modules.keys())
        templates = self.env['ai.org.template'].search(
            [('active', '=', True)])
        best = None
        best_score = -1
        for template in templates:
            try:
                structure = template.structure_json or {}
                detect = set(structure.get('detect_modules', []))
                score = len(detect & installed)
                if score > best_score:
                    best_score = score
                    best = template
            except Exception:
                continue
        return best

    # ════════════════════════════════════════════
    # FAS 2: FRÅGOR
    # ════════════════════════════════════════════

    def _get_interview_questions(self):
        """Hämta intervjufrågor från templaten, begränsad till installerade moduler."""
        questions = []
        if not self.template_id:
            return questions
        structure = self.template_id.structure_json or {}
        modules = set(self.detected_modules.keys())
        for qgroup in structure.get('interview_questions', []):
            module = qgroup.get('module')
            if module and module not in modules:
                continue
            for q in qgroup.get('questions', [])[:3]:  # Max 3 per modul
                questions.append({
                    'id': q.get('id'),
                    'question': q.get('question'),
                    'options': q.get('options', []),
                    'if_yes': q.get('if_yes'),
                    'if_none': q.get('if_none'),
                })
        return questions

    def action_prepare_questions(self):
        """Fas 2a: Förbered frågorna."""
        self.ensure_one()
        questions = self._get_interview_questions()
        # Hoppa över frågor som redan besvarats
        answers = dict(self.answers or {})
        remaining = [q for q in questions if q['id'] not in answers]
        self.write({
            'state': 'interviewing',
            'total_questions': len(remaining),
            'question_index': 0,
        })
        if remaining:
            self.current_question = remaining[0]['question']
        return len(remaining)

    def action_answer(self, question_id, answer):
        """Fas 2b: Spara ett svar och gå vidare."""
        self.ensure_one()
        answers = dict(self.answers or {})
        answers[question_id] = answer
        self.write({'answers': answers})

        questions = self._get_interview_questions()
        remaining = [q for q in questions if q['id'] not in answers]
        if remaining:
            self.write({
                'question_index': len(answers),
                'current_question': remaining[0]['question'],
            })
            return remaining[0]
        else:
            # Alla frågor besvarade
            self.state = 'analyzing'
            return None

    # ════════════════════════════════════════════
    # FAS 3: FÖRSLAG
    # ════════════════════════════════════════════

    def action_generate_proposal(self):
        """Fas 3: Generera organisationsförslaget från template + svar."""
        self.ensure_one()
        answers = self.answers or {}
        structure = self.template_id.structure_json or {} if self.template_id else {}
        generator = structure.get('proposal_generator', {})

        departments = {}
        # Bygg avdelningar från generators
        for q_id, answer in answers.items():
            q_rules = generator.get(q_id, {})
            if answer in q_rules:
                rule = q_rules[answer]
                if rule.get('skip'):
                    continue
                for coworker in rule.get('coworkers', []):
                    dept_name = rule.get('department',
                        self._default_department_for_question(q_id))
                    dept = departments.setdefault(dept_name, {
                        'name': dept_name,
                        'coworkers': [],
                    })
                    coworker['source_question'] = q_id
                    dept['coworkers'].append(coworker)

        # Företagsmål från template default_goals
        company_goals = structure.get('default_goals', [])

        # Mission/values-förslag
        mission, values = self._suggest_identity(answers)

        proposal = {
            'mission': mission,
            'values': values,
            'departments': list(departments.values()),
            'company_goals': company_goals,
        }
        self.write({
            'state': 'proposal',
            'proposal_json': proposal,
        })
        return proposal

    def _default_department_for_question(self, question_id):
        """Mappa fråga till standardavdelning."""
        q_to_dept = {
            'account_moms': 'Ekonomi',
            'account_invoices': 'Ekonomi',
            'account_bookkeeping': 'Ekonomi',
            'hr_payroll': 'Personal',
            'crm_leads': 'Försäljning',
            'sale_offers': 'Försäljning',
            'project_count': 'Projekt',
            'project_timesheets': 'Projekt',
        }
        return q_to_dept.get(question_id, 'Generellt')

    def _suggest_identity(self, answers):
        """Föreslå mission/values från intervjusvaren."""
        # Grundtext — kan förfinas med LLM om tillgängligt
        mission = (
            f'{self.company_id.name or "Vårt företag"} levererar värde '
            'genom att kombinera expertis med smarta AI-lösningar.'
        )
        values = (
            'Kvalitet och noggrannhet i allt vi gör.\n'
            'Personlig service med stöd av modern teknik.'
        )
        try:
            # LLM-förfinad version om provider finns
            modules_text = ', '.join(
                d.get('description', m) for m, d in
                (self.detected_modules or {}).items()) or 'okänt'
            prompt = (
                f'Företag: {self.company_id.name}\\n'
                f'Installerade moduler: {modules_text}\\n'
                f'Webbplats: {(self.website_summary or "")[:1000]}\\n'
                f'Svar: {json.dumps(answers, ensure_ascii=False)[:1500]}\\n\\n'
                'Generera mission (1 mening) och values (3 korta punkter). '
                'Svara exakt i JSON: {"mission": "...", "values": "..."}'
            )
            result = self.env['ai.provider']._call_llm(prompt)
            if result:
                data = json.loads(result) if isinstance(result, str) else result
                mission = data.get('mission', mission)
                values = data.get('values', values)
        except Exception as e:
            _logger.warning('LLM identity suggestion failed (using fallback): %s', e)
        return mission, values

    # ════════════════════════════════════════════
    # FAS 4: SKAPA ORGANISATIONEN
    # ════════════════════════════════════════════

    def action_create_organization(self):
        """Fas 4: Skapa departments, coworkers, agents, tasks, goals.

        Körs med sudo() eftersom det är en administrativ setup-operation
        som skapar records i flera modeller (hr, ai.*) oavsett den
        inloggade användarens rättigheter.
        """
        self.ensure_one()
        self = self.sudo()
        self.state = 'creating'
        proposal = self.proposal_json or {}

        # 1. Uppdatera mission/values
        if proposal.get('mission'):
            self.company_id.sudo().write({
                'company_mission': f'<p>{proposal["mission"]}</p>'})
        if proposal.get('values'):
            self.company_id.sudo().write({
                'company_values': f'<p>{proposal["values"]}</p>'})

        # 2. Skapa company-goals
        company_goals = proposal.get('company_goals', [])
        for goal_data in company_goals:
            goal = self._create_goal(goal_data, level='company')
            if goal:
                self.created_goal_ids = [(4, goal.id)]

        # 3. Skapa departments + coworkers
        for dept_data in proposal.get('departments', []):
            department = self._create_department(dept_data)
            if department:
                self.created_department_ids = [(4, department.id)]
                # Skapa coworkers i avdelningen
                for cw_data in dept_data.get('coworkers', []):
                    coworker = self._create_coworker(cw_data, department)
                    if coworker:
                        self.created_coworker_ids = [(4, coworker.id)]

        self.state = 'completed'
        _logger.info('Onboarding %s completed: %d departments, %d coworkers',
                     self.id, len(self.created_department_ids),
                     len(self.created_coworker_ids))
        return True

    def _create_goal(self, goal_data, level='company', department=None):
        """Skapa ett ai.org.goal med key results."""
        try:
            goal = self.env['ai.org.goal'].sudo().create({
                'name': goal_data.get('name', 'Mål'),
                'description': goal_data.get('description'),
                'level': level,
                'status': 'active',
                'department_id': department.id if department else False,
                'deadline': goal_data.get('deadline'),
                'company_id': self.company_id.id,
            })
            for kr_data in goal_data.get('key_results', []):
                self.env['ai.org.key_result'].sudo().create({
                    'goal_id': goal.id,
                    'name': kr_data.get('name', 'KR'),
                    'target_value': kr_data.get('target', 100.0),
                    'current_value': 0.0,
                    'unit': kr_data.get('unit', '%'),
                })
            # Koppla till company-goal om department-nivå
            return goal
        except Exception as e:
            _logger.error('Goal creation failed: %s', e)
            return False

    def _create_department(self, dept_data):
        """Skapa en hr.department."""
        name = dept_data.get('name', 'Ny avdelning')
        try:
            dept = self.env['hr.department'].sudo().create({
                'name': name,
                'company_id': self.company_id.id,
            })
            return dept
        except Exception as e:
            _logger.error('Department creation failed for %s: %s', name, e)
            return False

    def _create_coworker(self, cw_data, department):
        """Skapa en ai.coworker med agent, skills, tools och tasks."""
        name = cw_data.get('name', 'AI-medarbetare')
        mode = cw_data.get('mode', 'single')
        agent_data = cw_data.get('agent', {})
        try:
            # Skapa agent
            agent = self.env['ai.agent'].sudo().create({
                'name': agent_data.get('name', name),
                'ai_role': cw_data.get('description', 'AI-medarbetare'),
                'status': 'active',
            })
            # Skapa coworker
            coworker = self.env['ai.coworker'].sudo().create({
                'name': name,
                'description': cw_data.get('description',
                    f'AI-medarbetare inom {department.name}'),
                'status': 'active',
                'department_id': department.id,
                'orchestration_mode': mode,
                'heartbeat_enabled': True,
                'inject_company_memory': True,
            })
            # Koppla agent
            self.env['ai.coworker.agent'].sudo().create({
                'coworker_id': coworker.id,
                'agent_id': agent.id,
                'role': 'lead',
            })
            # Skapa init_types: web_ui + cron (heartbeat)
            InitType = self.env['ai.coworker.init_type']
            InitType.create({'coworker_id': coworker.id, 'init_type': 'web_ui', 'active': True})
            InitType.create({'coworker_id': coworker.id, 'init_type': 'cron', 'active': True,
                             'cron_interval_number': 5, 'cron_interval_type': 'minutes'})

            # Skapa initiala tasks så coworkern börjar jobba
            initial_tasks = cw_data.get('initial_tasks') or [
                f'Kartlägg arbetsflödet inom {cw_data.get("description", name)}',
                'Genomför första veckans arbete och rapportera',
            ]
            for task_text in initial_tasks:
                self._create_task(task_text, coworker, department)

            return coworker
        except Exception as e:
            _logger.error('Coworker creation failed for %s: %s', name, e)
            return False

    def _create_task(self, task_text, coworker, department):
        """Skapa en initial ai.org.task för en coworker."""
        try:
            task = self.env['ai.org.task'].sudo().create({
                'name': task_text,
                'description': f'Initial uppgift från onboarding för {coworker.name}',
                'coworker_id': coworker.id,
                'status': 'todo',
                'priority': '1',
                'source': 'onboarding',
                'company_id': self.company_id.id,
            })
            self.created_task_ids = [(4, task.id)]
            return task
        except Exception as e:
            _logger.error('Task creation failed: %s', e)
            return False

    # ════════════════════════════════════════════
    # ACTIONS
    # ════════════════════════════════════════════

    def action_start_interview(self):
        """Starta onboarding — öppna wizard för intervju."""
        self.ensure_one()
        coworker = self.coworker_id or self.env['ai.coworker'].search([
            ('is_default', '=', True)], limit=1)
        if not coworker:
            raise models.ValidationError(
                _('No default coworker found. Install ai_agent_core first.'))
        self.write({
            'coworker_id': coworker.id,
            'state': 'scanning',
        })
        # Skanna och öppna wizard
        self.action_scan()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'ai.onboarding.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_onboarding_id': self.id},
        }

    def action_restart(self):
        """Starta om onboarding — rensa och börja om."""
        self.ensure_one()
        self.write({
            'state': 'draft',
            'detected_modules': {},
            'website_summary': False,
            'answers': {},
            'proposal_json': {},
            'adjustments': {},
            'current_question': False,
            'question_index': 0,
            'total_questions': 0,
            'interview_session_id': False,
        })
        return True

    def action_cancel(self):
        self.write({'state': 'cancelled'})
