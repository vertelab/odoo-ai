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
        ('ignored', 'Ignored'),
    ], default='new')

    # Result if acted upon
    resulting_quest_id = fields.Many2one('ai.quest', string='Created Quest')
    resulting_ticket_id = fields.Integer('Ticket ID')
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
             ('invoice_date', '<', fields.Date.today() + ' - 90 days')],
             'utkast-fakturor äldre än 90 dagar', 'cleanup', 0.8),
            ('sale.order', [('state', '=', 'draft'),
             ('create_date', '<', fields.Date.today() + ' - 30 days')],
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
        error_sessions = self.env['ai.quest.session'].search([
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
        """Create an ai.quest from this candidate."""
        self.ensure_one()
        quest = self.env['ai.quest'].create({
            'name': f'ONBOARD: {self.description[:50]}',
            'init_type': 'manual',
            'description': self.description,
            'status': 'draft',
        })
        self.resulting_quest_id = quest.id
        self.status = 'created_quest'
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'ai.quest',
            'res_id': quest.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_notify(self):
        """Send a notification about this candidate."""
        self.ensure_one()
        self.env['mail.message'].create({
            'subject': f'ONBOARD upptäckt: {self.description[:80]}',
            'body': f"**ONBOARD upptäckte:** {self.description}\n\n"
                    f"Typ: {self.suggested_quest_type}\n"
                    f"Källa: {self.source}\n"
                    f"Confidence: {self.confidence:.0%}\n\n"
                    f"[Skapa quest][Ignorera]",
            'model': 'ai.onboard.candidate',
            'res_id': self.id,
        })
        return True

    def action_ignore(self):
        """Ignore this candidate."""
        self.ensure_one()
        self.status = 'ignored'
