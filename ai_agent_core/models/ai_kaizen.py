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

    coworker_id = fields.Many2one('ai.coworker', required=True, ondelete='cascade',
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

    # Nudge metrics (strategy-nudge-engine)
    nudge_metrics = fields.Json('Nudge Metrics',
        help='Per-department nudge effectiveness data: '
             '{department: {nudge_type: {delivered, opened, converted, trend}}}')

    @api.depends('coworker_id.name', 'week_start')
    def _compute_display_name(self):
        for r in self:
            quest_name = r.coworker_id.name if r.coworker_id else '?'
            week = r.week_start.isoformat() if r.week_start else '?'
            r.display_name = f'Kaizen: {quest_name} — vecka {week}'

    def generate_weekly_report(self, quest=None):
        """Cron: generate kaizen reports for all (or one) active quests."""
        if quest:
            coworkers = quest
        else:
            coworkers = self.env['ai.coworker'].search([('status', '=', 'active')])

        today = date.today()
        week_start = today - timedelta(days=today.weekday() + 7)  # Last Monday
        week_end = week_start + timedelta(days=6)  # Last Sunday

        created = 0
        for q in quests:
            # Skip if already generated this week
            existing = self.search([
                ('coworker_id', '=', q.id),
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
        """Create a single kaizen report for one coworker."""
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
            'coworker_id': coworker.id,
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
                finding = self.env['ai.kaizen.finding'].create({
                    'report_id': report.id,
                    **f,
                })
                # HITL: propose orchestration skill improvement
                try:
                    report._generate_skill_suggestion(f)
                except Exception as se:
                    _logger.warning('Skill suggestion failed: %s', se)
        except Exception as e:
            _logger.warning('Kaizen analysis failed for %s: %s', coworker.name, e)
            # Still create the report even if analysis fails

        # Generate report text
        report.report_text = report._render_report()
        report.status = 'generated'

        # Post to quest
        if quest:
            coworker.message_post(
                body=report.report_text,
                message_type='notification',
            )

        # Mark ONBOARD candidates as presented (Hole 2)
        onboard_candidates = self.env['ai.onboard.candidate'].search([
            ('status', '=', 'new'),
        ])
        if onboard_candidates:
            onboard_candidates.write({
                'status': 'presented',
                'presented_at_kaizen': report.id,
            })
            # Append ONBOARD section to report
            onboard_text = _render_onboard_section(onboard_candidates)
            report.report_text = (report.report_text or '') + onboard_text
            report.coworker_id.message_post(
                body=onboard_text,
                message_type='notification',
            )

        _logger.info('Kaizen report for %s: %d sessions, %d errors, %d findings',
                     coworker.name, week_data['session_count'],
                     week_data['error_count'], len(report.finding_ids))
        return report

    def _gather_week_data(self, quest, week_start, week_end):
        """Gather metrics for the specified week."""
        week_start_dt = datetime.combine(week_start, datetime.min.time())
        week_end_dt = datetime.combine(week_end, datetime.max.time())

        lines = self.env['ai.coworker.session.line'].search([
            ('session_id.coworker_id', '=', coworker.id),
            ('create_date', '>=', week_start_dt),
            ('create_date', '<=', week_end_dt),
        ])

        sessions = self.env['ai.coworker.session'].search([
            ('coworker_id', '=', coworker.id),
            ('create_date', '>=', week_start_dt),
            ('create_date', '<=', week_end_dt),
        ])

        error_sessions = sessions.filtered(lambda s: s.status == 'error')

        # Feedback from ai.memory
        feedback = self.env['ai.memory'].search([
            ('quest_id', '=', coworker.id),
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

    def _generate_skill_suggestion(self, finding):
        """Generate an orchestration skill improvement suggestion (HITL).

        Looks for the coworker's orchestration skill and proposes a
        recipe_text update based on the finding. Stored as JSON on the
        finding so the human can review and apply via action_apply_to_skill().
        """
        coworker = self.coworker_id
        skill = None
        for s in coworker.skill_ids:
            if s.name and 'orchestration' in s.name.lower():
                skill = s
                break
        if not skill:
            return

        import json
        suggested_recipe = skill.recipe_text
        notes = ''
        ftext = (finding.get('finding') or '').lower()

        # Heuristics: propose recipe additions based on finding category
        if 'fel' in ftext or 'error' in ftext:
            suggested_recipe += (
                '\n\n## Felhantering\n'
                '- Vid misslyckad delegation, försök en annan specialist eller återgå till användaren.\n'
                '- Logga fel för kaizen-analys.'
            )
            notes = f"Lade till felhantering: {finding.get('finding', '')[:200]}"
        elif 'kostnad' in ftext or 'cost' in ftext or 'token' in ftext:
            suggested_recipe += (
                '\n\n## Kostnadseffektivitet\n'
                '- Delegera endast när specialisten tillför värde; annars svara direkt.\n'
                '- Begränsa kontext per delegation.'
            )
            notes = f"Lade till kostnadsoptimering: {finding.get('finding', '')[:200]}"
        elif 'feedback' in ftext or 'förbättring' in ftext:
            notes = f"Feedback att beakta: {finding.get('finding', '')[:200]}"

        if suggested_recipe != skill.recipe_text:
            self.env['ai.kaizen.finding'].create({
                'report_id': self.id,
                'severity': 'low',
                'category': 'skill_gap',
                'finding': f"Föreslår förbättring av orchestration-skill '{skill.name}'",
                'recommendation': 'Granska och applicera förslaget på skillen.',
                'evidence': finding.get('evidence', ''),
                'skill_suggestion': json.dumps({
                    'skill_id': skill.id,
                    'suggested_recipe': suggested_recipe,
                    'notes': notes,
                }),
            })

    def _render_report(self):
        """Render kaizen report as text."""
        self.ensure_one()
        quest_name = self.coworker_id.name

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
    # Orchestration skill suggestion (JSON: {skill_id, suggested_recipe, notes})
    skill_suggestion = fields.Text('Skill Suggestion',
        help='JSON: {skill_id, suggested_recipe, notes} for HITL skill update.')
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

    def action_apply_to_skill(self):
        """Apply approved skill suggestion to the orchestration skill (HITL)."""
        self.ensure_one()
        if self.status != 'approved':
            raise ValueError('Only approved findings can be applied to skills.')
        if not self.skill_suggestion:
            return False
        import json
        try:
            data = json.loads(self.skill_suggestion)
        except json.JSONDecodeError:
            return False
        skill = self.env['ai.skill'].browse(data.get('skill_id', 0))
        if not skill.exists():
            return False
        skill.action_apply_kaizen_suggestion(
            suggested_recipe=data.get('suggested_recipe'),
            notes=data.get('notes', '') or f'Kaizen finding: {self.finding[:200]}')
        self.status = 'applied'
        return True

    def action_apply(self):
        """Apply the recommended fix."""
        self.ensure_one()
        quest = self.report_id.coworker_id

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


def _render_onboard_section(candidates):
    """Render ONBOARD findings as kaizen report section."""
    if not candidates:
        return ''

    lines = [
        "",
        "## 🆕 Upptäckta möjligheter (ONBOARD)",
    ]
    for c in candidates:
        icon = {
            'data_quality': '📊',
            'repetitive_task': '🔄',
            'module_gap': '🧩',
            'error_pattern': '❌',
            'integration': '🔌',
        }.get(c.source, '•')
        lines.append(f"\n{icon} **{c.description}**")
        lines.append(f"  Typ: {c.suggested_quest_type} | Confidence: {c.confidence:.0%}")
        if c.record_count:
            lines.append(f"  Antal: {c.record_count} poster")

    return '\n'.join(lines)
