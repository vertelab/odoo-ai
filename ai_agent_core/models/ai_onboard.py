# -*- coding: utf-8 -*-
"""
ONBOARD — Mine Odoo instance for quest candidates (TASK-005, Hole 2).

Scans the instance for:
- Data quality problems
- Repetitive manual tasks
- Module gaps
- Error patterns
- Integration opportunities (helpdesk, project, mgmtsystem)

Findings are stored as ai.onboard.candidate and presented at kaizen time.
"""

import json
import logging
from datetime import timedelta

from odoo import models, fields, api, _

_logger = logging.getLogger(__name__)


class AIOnboardCandidate(models.Model):
    _name = 'ai.onboard.candidate'
    _description = 'ONBOARD Discovery'
    _order = 'confidence desc, discovered_at desc'

    source = fields.Selection([
        ('data_quality', 'Data Quality'),
        ('repetitive_task', 'Repetitive Task'),
        ('module_gap', 'Module Gap'),
        ('error_pattern', 'Error Pattern'),
        ('integration', 'Integration Gap'),
    ], required=True, string='Source')
    description = fields.Text('Description', required=True)
    suggested_quest_type = fields.Selection([
        ('monitoring', 'Monitoring'),
        ('automation', 'Automation'),
        ('cleanup', 'Data Cleanup'),
        ('report', 'Report Generation'),
        ('integration', 'Integration'),
    ], string='Suggested Quest Type')
    confidence = fields.Float('Confidence', default=0.5)
    evidence = fields.Text('Evidence')
    source_module = fields.Char('Source Module')
    record_count = fields.Integer('Affected Records', default=0)

    discovered_at = fields.Datetime(default=lambda self: fields.Datetime.now())
    status = fields.Selection([
        ('new', 'New'),
        ('presented', 'Presented at Kaizen'),
        ('created_quest', 'Quest Created'),
        ('created_ticket', 'Ticket Created'),
        ('created_action', 'Action Created'),
        ('created_nonconformity', 'Nonconformity Created'),
        ('ignored', 'Ignored'),
    ], default='new')

    # Result if acted upon
    resulting_coworker_id = fields.Many2one('ai.coworker', string='Created Quest')
    resulting_ticket_id = fields.Integer('Ticket ID')
    resulting_action_id = fields.Many2one('ir.actions.server', string='Created Action')
    resulting_nonconformity_id = fields.Integer('Nonconformity ID')
    presented_at_kaizen = fields.Many2one('ai.kaizen.report',
        string='Presented at Kaizen')

    def scan_instance(self):
        """Main scan: run all detectors and create candidates."""
        self._scan_data_quality()
        self._scan_repetitive_tasks()
        self._scan_error_patterns()
        self._scan_integration_gaps()
        _logger.info('ONBOARD scan complete')

    def _scan_data_quality(self):
        """Scan Odoo models for data quality issues."""
        checks = [
            # (model, domain, description, quest_type, confidence)
            ('res.partner', [('email', '=', False), ('active', '=', True)],
             'partners utan email', 'cleanup', 0.7),
            ('res.partner', [('phone', '=', False), ('mobile', '=', False),
             ('active', '=', True)],
             'partners utan telefon', 'cleanup', 0.4),
            ('account.move', [('state', '=', 'draft'),
             ('invoice_date', '<', fields.Date.today() - timedelta(days=90))],
             'utkast-fakturor äldre än 90 dagar', 'cleanup', 0.8),
            ('sale.order', [('state', '=', 'draft'),
             ('create_date', '<', fields.Date.today() - timedelta(days=30))],
             'utkast-ordrar äldre än 30 dagar', 'cleanup', 0.6),
        ]

        for model_name, domain, desc, quest_type, confidence in checks:
            try:
                model = self.env.get(model_name)
                if not model:
                    continue
                count = model.search_count(domain)
                if count > 5:
                    self.create({
                        'source': 'data_quality',
                        'description': f'{count} {desc} hittades i {model_name}',
                        'suggested_quest_type': quest_type,
                        'confidence': confidence,
                        'evidence': f'Model: {model_name}, Count: {count}, Criteria: {desc}',
                        'source_module': model_name,
                        'record_count': count,
                    })
            except Exception as e:
                _logger.debug('ONBOARD data_quality skip %s: %s', model_name, e)

    def _scan_repetitive_tasks(self):
        """Find server actions that are run frequently — candidates for automation."""
        actions = self.env['ir.actions.server'].search([
            ('usage', '!=', 'ir_cron'),  # Not already automated
        ])
        for action in actions:
            # Check if this action is referenced in any menu items
            menu_count = self.env['ir.ui.menu'].search_count([
                ('action', 'ilike', f'ir.actions.server,{action.id}')
            ])
            if menu_count > 0:
                # Has a menu item — user might be running this manually often
                self.create({
                    'source': 'repetitive_task',
                    'description': f'Server action "{action.name}" är tillgänglig via meny — '
                                 f'kandidat för automatisering eller AI-quest',
                    'suggested_quest_type': 'automation',
                    'confidence': 0.5,
                    'evidence': f'Server action ID {action.id}, model: {action.model_id.name}',
                    'source_module': action.model_id.model if action.model_id else '',
                })

    def _scan_error_patterns(self):
        """Scan quest session errors for patterns."""
        error_sessions = self.env['ai.coworker.session'].search([
            ('status', '=', 'error'),
            ('create_date', '>=', fields.Datetime.now() + ' - 30 days'),
        ])
        if not error_sessions:
            return

        # Group by finish_reason
        reasons = {}
        for s in error_sessions:
            reason = s.finish_reason or 'unknown'
            reasons[reason] = reasons.get(reason, 0) + 1

        for reason, count in reasons.items():
            if count >= 3:
                self.create({
                    'source': 'error_pattern',
                    'description': f'{count} sessioner misslyckades med orsak: "{reason}" '
                                 f'(senaste 30 dagarna)',
                    'suggested_quest_type': 'monitoring',
                    'confidence': min(count / 10, 0.9),
                    'evidence': f'Finish reason: {reason}, Count: {count}',
                    'record_count': count,
                })

    def _scan_integration_gaps(self):
        """Check for installed modules that ONBOARD can integrate with."""
        integrations = {
            'helpdesk': {
                'model': 'helpdesk.ticket',
                'description': 'Återkommande helpdesk-ämnen → quest-kandidater',
            },
            'project': {
                'model': 'project.task',
                'description': 'Projekt-uppgifter utan automation → quest-kandidater',
            },
            'mgmtsystem': {
                'model': 'mgmtsystem.nonconformity',
                'description': 'Avvikelser som kan automatgranskas → quest-kandidater',
            },
        }

        for module_key, info in integrations.items():
            model = self.env.get(info['model'])
            if model:
                # Module is installed — create an integration candidate
                self.create({
                    'source': 'integration',
                    'description': f'{module_key} är installerat. {info["description"]}',
                    'suggested_quest_type': 'integration',
                    'confidence': 0.6,
                    'evidence': f'Module: {module_key}, Model: {info["model"]}',
                    'source_module': module_key,
                })

    def action_create_quest(self):
        """Create an ai.coworker from this candidate."""
        self.ensure_one()
        quest = self.env['ai.coworker'].create({
            'name': f'ONBOARD: {self.description[:50]}',
            'init_type': 'manual',
            'description': self.description,
            'status': 'draft',
        })
        self.resulting_coworker_id = quest.id
        self.status = 'created_quest'
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'ai.coworker',
            'res_id': quest.id,
            'view_mode': 'form',
            'views': [[False, 'form']],
            'target': 'current',
        }

    def action_notify(self):
        """Send a notification about this candidate to admin users."""
        self.ensure_one()
        self.message_post(
            subject=f'ONBOARD upptäckt: {self.description[:80]}',
            body=f"**ONBOARD upptäckte:** {self.description}\n\n"
                 f"Typ: {self.suggested_quest_type}\n"
                 f"Källa: {self.source}\n"
                 f"Confidence: {self.confidence:.0%}\n"
                 f"Antal poster: {self.record_count or 'N/A'}",
            message_type='notification',
            partner_ids=[(4, uid) for uid in
                self.env.ref('base.group_system').users.ids],
        )
        return True

    def action_create_server_action(self):
        """Create an ir.actions.server from this candidate.
        
        The action is created as a draft that the admin can configure.
        Useful when the candidate identifies a repetitive task that
        should be automated but doesn't need a full AI quest.
        """
        self.ensure_one()

        # Try to determine the target model from evidence
        model_name = 'ir.actions.server'  # default
        if self.source_module:
            model = self.env.get(self.source_module)
            if model:
                model_name = self.source_module

        action = self.env['ir.actions.server'].create({
            'name': f'ONBOARD: {self.description[:50]}',
            'model_id': self.env['ir.model'].search(
                [('model', '=', model_name)], limit=1).id,
            'state': 'code',
            'code': f'# ONBOARD-generated action\n'
                    f'# {self.description}\n'
                    f'# Source: {self.source}\n'
                    f'# Auto-generated — configure before use\n'
                    f'# model: {model_name}\n'
                    f'raise NotImplementedError("Configure this action")',
        })
        self.resulting_action_id = action.id
        self.status = 'created_action'
        _logger.info('ONBOARD: created server action %s for candidate %s',
                     action.name, self.description[:50])
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'ir.actions.server',
            'res_id': action.id,
            'view_mode': 'form',
            'views': [[False, 'form']],
            'target': 'current',
        }

    def action_create_helpdesk_ticket(self):
        """Create a helpdesk ticket from this candidate.
        
        Only works if helpdesk module is installed.
        """
        self.ensure_one()
        ticket_model = self.env.get('helpdesk.ticket')
        if not ticket_model:
            _logger.warning('ONBOARD: helpdesk not installed, cannot create ticket')
            return self.action_notify()  # Fallback to notification

        # Find or create a team
        team = self.env['helpdesk.team'].search([], limit=1)
        if not team:
            team = self.env['helpdesk.team'].create({
                'name': 'AI ONBOARD',
            })

        ticket = ticket_model.create({
            'name': f'ONBOARD: {self.description[:80]}',
            'description': (
                f"**Upptäckt av ONBOARD**\n\n"
                f"{self.description}\n\n"
                f"Typ: {self.suggested_quest_type}\n"
                f"Källa: {self.source}\n"
                f"Confidence: {self.confidence:.0%}\n"
                f"Evidens: {self.evidence or 'N/A'}\n"
                f"Antal poster: {self.record_count or 'N/A'}"
            ),
            'team_id': team.id,
            'priority': '2' if self.confidence > 0.7 else '1',
        })
        self.resulting_ticket_id = ticket.id
        self.status = 'created_ticket'
        _logger.info('ONBOARD: created helpdesk ticket %s for candidate %s',
                     ticket.id, self.description[:50])
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'helpdesk.ticket',
            'res_id': ticket.id,
            'view_mode': 'form',
            'views': [[False, 'form']],
            'target': 'current',
        }

    def action_create_nonconformity(self):
        """Create an avvikelserapport (nonconformity) from this candidate.
        
        Only works if mgmtsystem module is installed.
        """
        self.ensure_one()
        nc_model = self.env.get('mgmtsystem.nonconformity')
        if not nc_model:
            _logger.warning('ONBOARD: mgmtsystem not installed, cannot create nonconformity')
            return self.action_notify()  # Fallback to notification

        nc = nc_model.create({
            'name': f'ONBOARD: {self.description[:80]}',
            'description': (
                f"Upptäckt av ONBOARD\n\n{self.description}\n\n"
                f"Typ: {self.suggested_quest_type}\n"
                f"Källa: {self.source}\n"
                f"Evidens: {self.evidence or 'N/A'}"
            ),
        })
        self.resulting_nonconformity_id = nc.id
        self.status = 'created_nonconformity'
        _logger.info('ONBOARD: created nonconformity %s for candidate %s',
                     nc.id, self.description[:50])
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'mgmtsystem.nonconformity',
            'res_id': nc.id,
            'view_mode': 'form',
            'views': [[False, 'form']],
            'target': 'current',
        }

    def action_ignore(self):
        """Ignore this candidate."""
        self.ensure_one()
        self.status = 'ignored'
