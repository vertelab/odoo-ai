# -*- coding: utf-8 -*-
"""
Kaizen — Weekly self-review agent (KAIZEN-001, Hole 1).

Per-quest weekly analysis of performance, errors, costs, and feedback.
Proposes improvements with evidence. Requires human approval.
"""

import json
import logging
from datetime import date, timedelta, datetime
from odoo import models, fields, api, _

_logger = logging.getLogger(__name__)


class AIKaizenReport(models.Model):
    _name = 'ai.kaizen.report'
    _description = 'Kaizen Weekly Report'
    _order = 'week_start desc'
    _inherit = ['mail.thread']

    quest_id = fields.Many2one('ai.quest', required=True, ondelete='cascade',
                                string='Quest')
    week_start = fields.Date('Week Starting', required=True)
    week_end = fields.Date('Week Ending', required=True)
    display_name = fields.Char(compute='_compute_display_name', store=True)

    # Metrics
    session_count = fields.Integer('Sessions', default=0)
    total_sys_tokens = fields.Integer('Systemtokens', default=0)
    total_input_tokens = fields.Integer('Input Tokens', default=0)
    total_output_tokens = fields.Integer('Output Tokens', default=0)
    error_count = fields.Integer('Errors', default=0)
    feedback_count = fields.Integer('Feedback Items', default=0)
    avg_response_tokens = fields.Integer('Avg Response Tokens', default=0)

    # Previous week comparison
    session_trend = fields.Float('Session Trend %', default=0.0)
    cost_trend = fields.Float('Cost Trend %', default=0.0)
    error_trend = fields.Float('Error Trend %', default=0.0)

    # Status
    status = fields.Selection([
        ('draft', 'Draft'), ('generated', 'Generated'),
        ('reviewed', 'Reviewed'), ('applied', 'Applied'),
    ], default='draft')

    # Report text (rendered markdown)
    report_text = fields.Text('Report')
    actions_json = fields.Text('Actions Taken',
        help='JSON: [{finding_id, approved, applied, result}]')

    # Findings
    finding_ids = fields.One2many('ai.kaizen.finding', 'report_id',
                                   string='Findings')

    @api.depends('quest_id.name', 'week_start')
    def _compute_display_name(self):
        for r in self:
            quest_name = r.quest_id.name if r.quest_id else '?'
            week = r.week_start.isoformat() if r.week_start else '?'
            r.display_name = f'Kaizen: {quest_name} — vecka {week}'

    def generate_weekly_report(self, quest=None):
        """Cron: generate kaizen reports for all (or one) active quests."""
        if quest:
            quests = quest
        else:
            quests = self.env['ai.quest'].search([('status', '=', 'active')])

        today = date.today()
        week_start = today - timedelta(days=today.weekday() + 7)  # Last Monday
        week_end = week_start + timedelta(days=6)  # Last Sunday

        created = 0
        for q in quests:
            # Skip if already generated this week
            existing = self.search([
                ('quest_id', '=', q.id),
                ('week_start', '=', week_start),
            ], limit=1)
            if existing:
                continue

            report = self._create_report_for_quest(q, week_start, week_end)
            if report:
                created += 1

        _logger.info('Kaizen: generated %d reports for week %s',
                     created, week_start.isoformat())
        return created

    def _create_report_for_quest(self, quest, week_start, week_end):
        """Create a single kaizen report for one quest."""
        # Gather data
        week_data = self._gather_week_data(quest, week_start, week_end)
        prev_data = self._gather_previous_week(quest, week_start)

        if not week_data['session_count'] and not week_data['feedback_count']:
            return False  # No activity, skip

        # Calculate trends
        if prev_data and prev_data['session_count']:
            week_data['session_trend'] = _trend(
                week_data['session_count'], prev_data['session_count'])
            week_data['cost_trend'] = _trend(
                week_data['total_sys_tokens'], prev_data['total_sys_tokens'])
            week_data['error_trend'] = _trend(
                week_data['error_count'], prev_data['error_count'])

        # Create report
        report = self.create({
            'quest_id': quest.id,
            'week_start': week_start,
            'week_end': week_end,
            'status': 'draft',
            **{k: v for k, v in week_data.items()
               if k in self._fields},
        })

        # Generate findings (uses cheap LLM for analysis)
        try:
            findings = self._analyze_findings(quest, report, week_data)
            for f in findings:
                self.env['ai.kaizen.finding'].create({
                    'report_id': report.id,
                    **f,
                })
        except Exception as e:
            _logger.warning('Kaizen analysis failed for %s: %s', quest.name, e)
            # Still create the report even if analysis fails

        # Generate report text
        report.report_text = report._render_report()
        report.status = 'generated'

        # Post to quest
        if quest:
            quest.message_post(
                body=report.report_text,
                message_type='notification',
            )

        _logger.info('Kaizen report for %s: %d sessions, %d errors, %d findings',
                     quest.name, week_data['session_count'],
                     week_data['error_count'], len(report.finding_ids))
        return report

    def _gather_week_data(self, quest, week_start, week_end):
        """Gather metrics for the specified week."""
        week_start_dt = datetime.combine(week_start, datetime.min.time())
        week_end_dt = datetime.combine(week_end, datetime.max.time())

        lines = self.env['ai.quest.session.line'].search([
            ('session_id.quest_id', '=', quest.id),
            ('create_date', '>=', week_start_dt),
            ('create_date', '<=', week_end_dt),
        ])

        sessions = self.env['ai.quest.session'].search([
            ('quest_id', '=', quest.id),
            ('create_date', '>=', week_start_dt),
            ('create_date', '<=', week_end_dt),
        ])

        error_sessions = sessions.filtered(lambda s: s.status == 'error')

        # Feedback from ai.memory
        feedback = self.env['ai.memory'].search([
            ('quest_id', '=', quest.id),
            ('category', '=', 'feedback'),
            ('create_date', '>=', week_start_dt),
            ('create_date', '<=', week_end_dt),
        ])

        total_tokens = sum(l.token_input + l.token_output for l in lines)
        total_sys = sum(l.token_sys or 0 for l in lines)

        return {
            'session_count': len(sessions),
            'total_sys_tokens': total_sys,
            'total_input_tokens': sum(l.token_input for l in lines),
            'total_output_tokens': sum(l.token_output for l in lines),
            'error_count': len(error_sessions),
            'feedback_count': len(feedback),
            'avg_response_tokens': (total_tokens // len(lines)) if lines else 0,
        }

    def _gather_previous_week(self, quest, current_week_start):
        """Gather previous week's data for trend comparison."""
        prev_start = current_week_start - timedelta(days=7)
        prev_end = current_week_start - timedelta(days=1)
        return self._gather_week_data(quest, prev_start, prev_end)

    def _analyze_findings(self, quest, report, data):
        """Generate findings from week data. Uses cheap LLM for analysis."""
        findings = []

        # Rule-based findings (fast, no LLM cost)
        if data['error_count'] > 0:
            pct = (data['error_count'] / data['session_count'] * 100) if data['session_count'] else 0
            if pct > 10:
                findings.append({
                    'severity': 'high',
                    'category': 'error',
                    'finding': f"{data['error_count']} av {data['session_count']} sessioner ({pct:.0f}%) hade fel",
                    'recommendation': f"Granska felorsakerna. Vanliga orsaker: timeout, tool crash, LLM refuse. "
                                     f"Överväg att lägga till felhantering eller justera timeout.",
                    'evidence': f"Errors: {data['error_count']}/{data['session_count']} sessions",
                })

        if data['cost_trend'] > 20:
            findings.append({
                'severity': 'medium',
                'category': 'cost',
                'finding': f"Systemtoken-förbrukningen ökade med {data['cost_trend']:.0f}% jämfört med förra veckan",
                'recommendation': "Överväg att byta till en billigare modell för rutinuppgifter, "
                                 "eller sätta ett månadstak.",
                'evidence': f"Denna vecka: {data['total_sys_tokens']:,} tokens. Trend: +{data['cost_trend']:.0f}%",
            })

        if data['feedback_count'] > 0:
            findings.append({
                'severity': 'low',
                'category': 'feedback',
                'finding': f"{data['feedback_count']} förbättringsförslag mottogs denna vecka",
                'recommendation': "Granska förbättringsförslagen och uppdatera questens beskrivning eller skills.",
                'evidence': f"Feedback count: {data['feedback_count']}",
            })

        return findings

    def _render_report(self):
        """Render kaizen report as text."""
        self.ensure_one()
        quest_name = self.quest_id.name

        lines = [
            f"📊 **Kaizen-rapport: {quest_name}**",
            f"Vecka {self.week_start.isoformat()} — {self.week_end.isoformat()}",
            "",
            "## 📈 Översikt",
            f"- {self.session_count} sessioner",
            f"- {self.total_sys_tokens:,} systemtokens",
            f"- {self.error_count} fel",
            f"- {self.feedback_count} förbättringsförslag",
        ]

        if self.session_trend:
            lines.append(f"- Session-trend: {self.session_trend:+.0f}%")
        if self.cost_trend:
            lines.append(f"- Kostnadstrend: {self.cost_trend:+.0f}%")

        # Findings by severity
        findings = self.finding_ids.sorted('severity', reverse=True)
        if findings:
            lines.append("")
            lines.append("## 🔍 Fynd")

            for f in findings:
                icon = {'high': '🔴', 'medium': '⚠️', 'low': '💡'}.get(f.severity, '•')
                lines.append(f"\n{icon} **{f.finding}** ({f.severity})")
                if f.recommendation:
                    lines.append(f"  → {f.recommendation}")
                if f.evidence:
                    lines.append(f"  *Evidens:* {f.evidence}")

        if not findings:
            lines.append("\n✅ Inga anmärkningar denna vecka.")

        return '\n'.join(lines)


class AIKaizenFinding(models.Model):
    _name = 'ai.kaizen.finding'
    _description = 'Kaizen Finding'
    _order = 'severity desc, id asc'

    report_id = fields.Many2one('ai.kaizen.report', required=True,
                                 ondelete='cascade', string='Report')
    severity = fields.Selection([
        ('low', '💡 Förslag'),
        ('medium', '⚠️ Varning'),
        ('high', '🔴 Kritisk'),
    ], required=True, default='low')
    category = fields.Selection([
        ('cost', 'Kostnad'),
        ('error', 'Fel'),
        ('performance', 'Prestanda'),
        ('skill_gap', 'Kompetensgap'),
        ('feedback', 'Feedback'),
    ], required=True, default='error')
    finding = fields.Text('Finding', required=True)
    recommendation = fields.Text('Recommendation')
    evidence = fields.Text('Evidence')
    status = fields.Selection([
        ('pending', 'Pending'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
        ('applied', 'Applied'),
    ], default='pending')

    def action_approve(self):
        self.status = 'approved'

    def action_reject(self):
        self.status = 'rejected'

    def action_apply(self):
        """Apply the recommended fix."""
        self.ensure_one()
        quest = self.report_id.quest_id

        if self.category == 'skill_gap':
            # Try to add a skill
            pass  # Complex — needs skill matching
        elif self.category == 'cost':
            # TODO: suggest model change
            pass
        elif self.category == 'error':
            # TODO: adjust timeout or error handling
            pass

        self.status = 'applied'


def _trend(current, previous):
    """Calculate percentage change, avoiding division by zero."""
    if not previous:
        return 0.0
    return ((current - previous) / previous) * 100
