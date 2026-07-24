# -*- coding: utf-8 -*-
"""ai.quest — standalone, no LangGraph. Uses AgentLoop."""

import json, logging, re, uuid
from odoo import models, fields, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


def _quest_is_accessible(quest, user):
    """Check if a user can access a quest via the web chat."""
    if user.has_group('base.group_system'):
        return True
    if quest.user_id and quest.user_id.id == user.id:
        return True
    if not quest.show_in_chat:
        return False
    if quest.group_ids:
        user_grp = set(user.groups_id.ids)
        quest_grp = set(quest.group_ids.ids)
        if not (user_grp & quest_grp):
            return False
    if quest.user_ids:
        if user.id not in quest.user_ids.ids:
            return False
    return True


INIT_TYPES = [
    ('manual', 'Manual'), ('chat', 'Chat with User'), ('channel', 'Chat with Channel'),
    ('cron', 'Scheduled Action'), ('server-action', 'Server Action'), ('mail', 'Mail'),
    ('powerbox', 'Powerbox'),
]


class AIQuest(models.Model):
    _name = 'ai.quest'
    _description = 'AI Quest'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'sequence asc, name asc'

    name = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    description = fields.Text(help='System prompt / quest purpose')
    sub_description = fields.Char('Short Description')
    active = fields.Boolean(default=True)
    color = fields.Integer(default=lambda self: __import__('random').randint(1, 11))

    status = fields.Selection([
        ('draft', 'Draft'), ('active', 'Active'), ('done', 'Done'), ('error', 'Error'),
    ], default='draft')

    init_type = fields.Selection(INIT_TYPES, required=True, default='manual')
    model_id = fields.Many2one('ir.model', string='Target Model')
    model_ids = fields.Many2many('ir.model', 'ai_quest_model_rel',
        'quest_id', 'model_id', string='Target Models',
        help='Models this quest can work with. For powerbox quests, '
             'the slash command only appears on records of these models.')
    model_name = fields.Char(related='model_id.model', readonly=True, store=True)
    filter_domain = fields.Char('Record Filter')

    agent_ids = fields.One2many('ai.quest.agent', 'quest_id', string='Agents')
    agent_count = fields.Integer(compute='_compute_agent_count')
    is_supervisor = fields.Boolean('Supervisor Mode')

    identity_id = fields.Many2one('ai.identity', string='Agent Identity')
    cron_id = fields.Many2one('ir.cron', string='Scheduled Action', ondelete='cascade')
    server_action_id = fields.Many2one('ir.actions.server', string='Server Action', ondelete='cascade')

    channel_id = fields.Many2one('discuss.channel', string='Channel')
    chat_user_id = fields.Many2one('res.users', string='Chat Bot User', readonly=True)
    allow_trigger_words = fields.Boolean('Use Activation Words')
    chat_trigger_words = fields.Text('Activation Words')

    use_chat_history = fields.Boolean(default=True)
    use_company_info = fields.Boolean(default=True)
    use_personal_info = fields.Boolean(default=True)
    use_personal_lang = fields.Boolean(default=True)
    use_time_context = fields.Boolean(default=True)
    chat_history_limit = fields.Integer(default=10)
    debug = fields.Boolean('Debug Mode')

    user_id = fields.Many2one('res.users', default=lambda self: self.env.user)
    company_id = fields.Many2one('res.company', default=lambda self: self.env.company)
    is_favorite = fields.Boolean('Favorite')

    # Access Control (quest-access-control)
    show_in_chat = fields.Boolean('Show in Web Chat', default=True)
    group_ids = fields.Many2many('res.groups', 'ai_quest_group_rel', 'quest_id', 'group_id', string='Access Groups')
    user_ids = fields.Many2many('res.users', 'ai_quest_user_rel', 'quest_id', 'user_id', string='Access Users')

    # Core loop migration
    use_core_loop = fields.Boolean('Use Core Loop', default=False)

    session_count = fields.Integer(compute='_compute_session_count')
    session_ids = fields.One2many('ai.quest.session', 'quest_id')

    # Systemtoken tracking
    session_line_ids = fields.One2many(
        'ai.quest.session.line', related='session_ids.session_line_ids',
        string='Session Lines',
        help='All message lines from all sessions of this quest')
    session_line_count = fields.Integer(
        'Systemtokens (månad)', compute='_compute_session_line_count',
        help='Total systemtokens consumed this calendar month')
    started_mtokens = fields.Integer(
        'Påbörjade M-tokens', compute='_compute_started_mtokens',
        help='ceil(session_line_count / 1_000_000) — for billing')

    # All-time totals (for XMLRPC billing)
    total_sys_tokens = fields.Integer(
        'Totalt systemtokens', default=0,
        help='All-time systemtoken consumption. Incremented per session line.')
    total_input_tokens = fields.Integer('Totalt input tokens', default=0)
    total_output_tokens = fields.Integer('Totalt output tokens', default=0)

    # Last month (from monthly_summary, for XMLRPC billing)
    last_month_sys_tokens = fields.Integer(
        'Förra månadens systemtokens',
        compute='_compute_last_month', store=False,
        help='Systemtokens consumed last calendar month')

    # Cap enforcement (Horisont 2)
    monthly_cap_mtokens = fields.Integer(
        'Månadstak (M systemtokens)', default=0,
        help='0 = unlimited. Cap in millions of systemtokens.')
    cap_warning_sent = fields.Boolean('Varning skickad')
    cap_exhausted = fields.Boolean('Tak överskridet')

    @api.constrains('monthly_cap_mtokens')
    def _check_monthly_cap(self):
        for r in self:
            if r.monthly_cap_mtokens < 0:
                raise UserError(_('Månadstak får inte vara negativt'))
            if r.monthly_cap_mtokens > 0 and r.started_mtokens > r.monthly_cap_mtokens:
                raise UserError(_(
                    'Kan inte sätta taket till %dM — redan förbrukat %dM denna månad'
                ) % (r.monthly_cap_mtokens, r.started_mtokens))

    def check_cap(self):
        """Check if quest has exceeded its monthly cap. Returns (warning, exhausted).
        
        Called after session lines are created. Posts discuss notification at 80%.
        Hard stop at 100%.
        """
        self.ensure_one()
        if not self.monthly_cap_mtokens:
            return False, False  # No cap set

        cap_tokens = self.monthly_cap_mtokens * 1_000_000
        used = self.session_line_count

        warning = used >= cap_tokens * 0.8
        exhausted = used >= cap_tokens

        if warning and not self.cap_warning_sent:
            self.cap_warning_sent = True
            self._notify_cap('warning', used, cap_tokens)

        if exhausted and not self.cap_exhausted:
            self.cap_exhausted = True
            self._notify_cap('exhausted', used, cap_tokens)

        return warning, exhausted

    def reset_cap(self):
        """Reset cap flags — called when user increases cap."""
        self.ensure_one()
        if not self.monthly_cap_mtokens:
            self.cap_warning_sent = False
            self.cap_exhausted = False
            return
        cap_tokens = self.monthly_cap_mtokens * 1_000_000
        used = self.session_line_count
        self.cap_warning_sent = used >= cap_tokens * 0.8
        self.cap_exhausted = used >= cap_tokens

    def consolidate_memories(self):
        """T9.1-T9.5: Daily memory consolidation.
        
        Groups memories by category, de-duplicates, ranks by importance.
        Updates identity.user_model with consolidated text.
        Auto-archives low-importance memories older than 30 days.
        """
        self.ensure_one()
        if not self.identity_id:
            return 0

        from datetime import date, timedelta
        cutoff = date.today() - timedelta(days=30)

        # Get active memories for this quest
        memories = self.env['ai.memory'].search([
            ('quest_id', '=', self.id),
            ('archived', '=', False),
        ])

        if not memories:
            return 0

        # Auto-archive old low-importance memories
        old_low = memories.filtered(
            lambda m: m.importance == 'low' and
            m.create_date and m.create_date.date() < cutoff
        )
        if old_low:
            old_low.write({'archived': True})
            memories -= old_low

        # Cap active memories at 50
        if len(memories) > 50:
            to_archive = memories.sorted(
                lambda m: {'high': 3, 'medium': 2, 'low': 1}.get(m.importance, 0)
            )[:len(memories) - 50]
            to_archive.write({'archived': True})
            memories -= to_archive

        # Group by category for consolidation
        by_category = {}
        for m in memories:
            cat = m.category or 'fact'
            if cat not in by_category:
                by_category[cat] = []
            by_category[cat].append(m.content[:200])

        # Build consolidated user_model text
        lines = []
        for cat, facts in sorted(by_category.items()):
            # Deduplicate similar facts
            unique = list(set(facts))[:5]
            if unique:
                lines.append(f"## {cat}")
                for f in unique:
                    lines.append(f"- {f}")

        if lines:
            self.identity_id.user_model = '\n'.join(lines)[:4000]

        # Mark consolidated
        memories.write({'consolidated': True})
        _logger.info('Consolidated %d memories for quest %s into %d categories',
                     len(memories), self.name, len(by_category))
        return len(memories)

    def _notify_cap(self, level, used, cap):
        """Post cap notification to quest's discuss channel + Zabbix."""
        self.ensure_one()
        pct = int(used / cap * 100) if cap else 0
        mtokens = self.started_mtokens

        if level == 'warning':
            msg = (
                f'⚠️ **Varning: AI-medarbetaren "{self.name}" har använt {pct}% '
                f'av månadstaket.**\n\n'
                f'Förbrukat: {mtokens}M av {self.monthly_cap_mtokens}M systemtokens.\n'
                f'Du kan höja taket i quest-inställningarna.'
            )
        else:
            msg = (
                f'🛑 **Tak överskridet: AI-medarbetaren "{self.name}" har nått '
                f'månadstaket på {self.monthly_cap_mtokens}M systemtokens.**\n\n'
                f'Agenten är nu pausad. Höj taket för att återaktivera.'
            )

        # Post via message_post (mail.thread)
        self.message_post(body=msg, message_type='notification')
        _logger.info('Cap %s for quest "%s": %d/%d (%.0f%%)',
                    level, self.name, used, cap, pct)

        # Send Zabbix event (if ai_agent_zabbix is installed)
        if level == 'exhausted':
            try:
                zabbix_configs = self.env['ai.zabbix.config'].search(
                    [('active', '=', True)], limit=1)
                if zabbix_configs:
                    zabbix_configs.notify_cap_exceeded(self)
            except Exception as e:
                _logger.warning('Zabbix notification failed (non-critical): %s', e)

    skill_copy_ids = fields.One2many('ai.quest.skill', 'quest_id',
        string='Skill Copies',
        help='Quest-specific copies of shared skills')
    last_run = fields.Datetime()

    tag_ids = fields.Many2many('product.tag', string='Tags')

    @api.depends('agent_ids')
    def _compute_agent_count(self):
        for r in self:
            r.agent_count = len(r.agent_ids)

    def _compute_session_count(self):
        for r in self:
            r.session_count = len(r.session_ids)

    @api.depends('session_line_ids.token_sys', 'session_line_ids.create_date')
    def _compute_session_line_count(self):
        """Sum of systemtokens for the current calendar month."""
        from datetime import date
        today = date.today()
        month_start = date(today.year, today.month, 1)
        for r in self:
            total = 0
            for line in r.session_line_ids:
                if line.create_date and line.create_date.date() >= month_start:
                    total += line.token_sys or 0
            r.session_line_count = total

    @api.depends('session_line_count')
    def _compute_started_mtokens(self):
        """Number of started millions (rounded up)."""
        import math
        for r in self:
            r.started_mtokens = math.ceil(r.session_line_count / 1_000_000) if r.session_line_count else 0

    def _compute_last_month(self):
        """Read last month's total from monthly_summary."""
        from datetime import date, timedelta
        today = date.today()
        first_of_month = date(today.year, today.month, 1)
        last_month_date = first_of_month - timedelta(days=1)
        last_month = last_month_date.strftime('%Y-%m')
        for r in self:
            summary = self.env['ai.quest.monthly_summary'].search([
                ('quest_id', '=', r.id),
                ('month', '=', last_month),
            ], limit=1)
            r.last_month_sys_tokens = summary.total_sys_tokens if summary else 0

    def action_get_agents(self):
        return {
            'name': 'Agents', 'type': 'ir.actions.act_window',
            'res_model': 'ai.agent', 'view_mode': 'kanban,list,form',
            'target': 'current',
            'domain': [('id', 'in', self.agent_ids.mapped('agent_id').ids)],
        }

    def action_get_sessions(self):
        return {
            'name': 'Sessions', 'type': 'ir.actions.act_window',
            'res_model': 'ai.quest.session', 'view_mode': 'list,form',
            'target': 'current',
            'domain': [('quest_id', '=', self.id)],
        }

    def action_monthly_overview(self):
        """Smart button: show this month's session lines with systemtoken breakdown."""
        self.ensure_one()
        from datetime import date
        today = date.today()
        month_start = date(today.year, today.month, 1)
        return {
            'name': f'Förbrukning — {self.name}',
            'type': 'ir.actions.act_window',
            'res_model': 'ai.quest.session.line',
            'view_mode': 'list,pivot',
            'target': 'current',
            'domain': [
                ('session_id.quest_id', '=', self.id),
                ('create_date', '>=', month_start.isoformat()),
            ],
            'context': {
                'pivot_measures': ['token_sys', 'token_input', 'token_output'],
                'pivot_column_groupby': ['model_real'],
                'pivot_row_groupby': ['session_id'],
            },
        }

    def get_billing_data(self):
        """Return billing data for external Odoo via XMLRPC.
        
        Returns JSON with active quests, user counts, and per-quest
        systemtoken consumption for the current month.
        """
        self.ensure_one()
        from datetime import date
        today = date.today()
        return {
            'quest_id': self.id,
            'quest_name': self.name,
            'month': today.strftime('%Y-%m'),
            'started_mtokens': self.started_mtokens,
            'session_line_count': self.session_line_count,
            'monthly_cap_mtokens': self.monthly_cap_mtokens,
            'cap_exhausted': self.cap_exhausted,
            'status': self.status,
            # All-time totals
            'total_sys_tokens': self.total_sys_tokens,
            'total_input_tokens': self.total_input_tokens,
            'total_output_tokens': self.total_output_tokens,
            # Last month
            'last_month_sys_tokens': self.last_month_sys_tokens,
        }

    # ── Powerbox (init_type='powerbox') ──

    _POWERBOX_SVG = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 530.06 530.06">'
        '<circle cx="265.03" cy="265.03" r="265.03" fill="#875a7b"/>'
        '<path d="M371.04 159.02H159.02c-14.58 0-26.41 11.83-26.41 26.41'
        'v159.02c0 14.58 11.83 26.41 26.41 26.41h212.02c14.58 0 '
        '26.41-11.83 26.41-26.41V185.43c0-14.58-11.83-26.41-26.41-26.41z'
        'm0 185.43H159.02V185.43h212.02v159.02z" fill="#ffffff"/>'
        '<path d="M212.02 238.43h105.62v26.41H212.02z'
        'M212.02 291.44h105.62v26.41H212.02z" fill="#ffffff"/>'
        '<circle cx="345.04" cy="265.03" r="26.41" fill="#ffffff"/>'
        '</svg>'
    )

    def powerbox(self, prompt, res_model=None, res_id=None, record=None):
        """Run quest as a powerbox — triggered from anywhere in Odoo.

        Args:
            prompt: The prompt to send to the AI
            res_model: Model name of the triggering record
            res_id: ID of the triggering record
            record: Optional record object (alternative to res_model+res_id)

        Returns:
            AI response text (markdown)

        Usage from server actions:
            result = quest.powerbox(
                prompt="Summarize this record",
                res_model=record._name,
                res_id=record.id
            )
        """
        self.ensure_one()
        if self.init_type != 'powerbox':
            _logger.warning('powerbox called on non-powerbox quest %s', self.name)

        # Resolve record
        if record is None and res_model and res_id:
            record = self.env[res_model].browse(int(res_id)).exists()

        # Build context from record
        record_context = ''
        if record:
            try:
                record_data = record.read()[0] if hasattr(record, 'read') else {}
                # Include key fields (skip binary, computed, related)
                key_fields = {}
                for field, value in record_data.items():
                    if value is not None and not isinstance(value, (bytes, bool)):
                        key_fields[field] = str(value)[:200]
                record_context = '\n'.join(
                    f'{k}: {v}' for k, v in list(key_fields.items())[:15]
                )
            except Exception:
                record_context = str(record)[:500]

        # Compile full prompt
        full_prompt = prompt
        if record_context:
            full_prompt = (
                f"Context (record {res_model or '?'}#{res_id or '?'}):\n"
                f"{record_context}\n\n"
                f"Task: {prompt}"
            )

        # Create session
        session = self.env['ai.quest.session'].create({
            'quest_id': self.id,
            'status': 'active',
            'user_id': self.env.user.id,
        })

        # Get model from first agent
        model = 'cerebras/gpt-oss-120b'  # default
        system_prompt = self.description or ''
        if self.identity_id:
            system_prompt = self.identity_id.system_prompt or system_prompt

        try:
            import asyncio
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                from odoo.addons.ai_agent_core.core.provider import BifrostProvider
                from odoo.addons.ai_agent_core.core.tools import ToolRegistry, builtin_tools
                from odoo.addons.ai_agent_core.core.loop import AgentLoop, AgentConfig

                provider = BifrostProvider(
                    base_url='http://192.168.11.150:8080/v1',
                    virtual_key='opencode',
                )
                tools = ToolRegistry()
                tools.register_many(builtin_tools())

                loop_obj = AgentLoop(
                    provider=provider,
                    tools=tools,
                    config=AgentConfig(
                        model=model,
                        system_prompt=system_prompt,
                        max_rounds=5,
                    ),
                )

                async def _run():
                    return await loop_obj.run(full_prompt)

                response = loop.run_until_complete(_run())
            finally:
                loop.close()

            # Save session line with token tracking
            if hasattr(response, 'text'):
                input_t = getattr(response, 'input_tokens', 0)
                output_t = getattr(response, 'output_tokens', 0)
                model_real = getattr(response, 'model', '')
                sys_mult = 1.0
                if model_real:
                    ai_model = self.env['ai.model'].search(
                        [('name', 'ilike', model_real)], limit=1)
                    if ai_model:
                        sys_mult = ai_model.sys_multiplier

                self.env['ai.quest.session.line'].create({
                    'session_id': session.id,
                    'role': 'assistant',
                    'content': response.text,
                    'token_input': input_t,
                    'token_output': output_t,
                    'model_real': model_real,
                    'sys_multiplier': sys_mult,
                })

                # Update session and quest totals
                session.token_input += input_t
                session.token_output += output_t
                session.status = 'done'

                self.total_input_tokens += input_t
                self.total_output_tokens += output_t
                self.total_sys_tokens += int((input_t + output_t) * sys_mult)

                import re
                import markdown
                answer = response.text
                answer = re.sub(
                    r'<think>.*?</think>', '', answer, flags=re.DOTALL)
                return markdown.markdown(answer) if markdown else answer

            # Fallback: return raw text
            return str(response) if response else ''

        except Exception as e:
            session.status = 'error'
            session.finish_reason = str(e)[:200]
            _logger.error('Powerbox error for quest %s: %s', self.name, e)
            raise UserError(_('Powerbox error: %s') % str(e))


class AIQuestMonthlySummary(models.Model):
    """Monthly systemtoken summary for billing and reporting (T3.5)."""
    _name = 'ai.quest.monthly_summary'
    _description = 'Monthly Quest Summary'
    _order = 'month desc, quest_id asc'
    _rec_name = 'display_name'

    quest_id = fields.Many2one('ai.quest', required=True, ondelete='cascade',
                                string='Quest')
    month = fields.Char('Month', required=True,
                         help='YYYY-MM format')
    display_name = fields.Char(compute='_compute_display_name', store=True)

    # Systemtoken consumption
    total_sys_tokens = fields.Integer('Systemtokens')
    started_mtokens = fields.Integer('Påbörjade M-tokens',
        compute='_compute_started_mtokens', store=True)
    total_input_tokens = fields.Integer('Input Tokens')
    total_output_tokens = fields.Integer('Output Tokens')
    session_count = fields.Integer('Sessions')

    # Model breakdown (JSON)
    model_breakdown = fields.Text('Per-Model Breakdown',
        help='JSON: {model_real: {tokens, systemtokens, sessions}}')

    # Cap info at time of summary
    monthly_cap_mtokens = fields.Integer('Cap (M tokens)')
    cap_exhausted_count = fields.Integer('Times Cap Exhausted')

    # Cost
    estimated_cost_usd = fields.Float('Est. Provider Cost (USD)')

    @api.depends('quest_id.name', 'month')
    def _compute_display_name(self):
        for r in self:
            quest_name = r.quest_id.name if r.quest_id else '?'
            r.display_name = f'{quest_name} — {r.month}'

    @api.depends('total_sys_tokens')
    def _compute_started_mtokens(self):
        import math
        for r in self:
            r.started_mtokens = math.ceil(r.total_sys_tokens / 1_000_000) if r.total_sys_tokens else 0

    def generate_monthly_summaries(self, month=None):
        """Cron: create monthly summaries for all active quests.
        
        Called at month rollover. Aggregates session_line data for the
        specified month (default: previous month).
        """
        from datetime import date, timedelta
        if not month:
            today = date.today()
            first_of_month = date(today.year, today.month, 1)
            last_month = first_of_month - timedelta(days=1)
            month = last_month.strftime('%Y-%m')

        year, m = month.split('-')
        month_start = f'{year}-{m}-01'
        month_end = f'{year}-{int(m)+1}-01' if int(m) < 12 else f'{int(year)+1}-01-01'

        quests = self.env['ai.quest'].search([('status', '=', 'active')])
        created = 0
        for quest in quests:
            # Check if summary already exists
            existing = self.search([
                ('quest_id', '=', quest.id),
                ('month', '=', month),
            ], limit=1)
            if existing:
                continue

            # Aggregate session lines for this month
            lines = self.env['ai.quest.session.line'].search([
                ('session_id.quest_id', '=', quest.id),
                ('create_date', '>=', month_start),
                ('create_date', '<', month_end),
            ])
            if not lines:
                continue

            # Per-model breakdown
            model_data = {}
            for line in lines:
                model = line.model_real or 'unknown'
                if model not in model_data:
                    model_data[model] = {'tokens': 0, 'systemtokens': 0, 'sessions': set()}
                model_data[model]['tokens'] += (line.token_input + line.token_output)
                model_data[model]['systemtokens'] += (line.token_sys or 0)
                if line.session_id:
                    model_data[model]['sessions'].add(line.session_id.id)

            # Convert sets to counts
            for model in model_data:
                model_data[model]['session_count'] = len(model_data[model]['sessions'])
                del model_data[model]['sessions']

            total_sys = sum(d['systemtokens'] for d in model_data.values())
            total_in = sum(l.token_input for l in lines)
            total_out = sum(l.token_output for l in lines)
            sessions = len(set(l.session_id.id for l in lines))

            self.create({
                'quest_id': quest.id,
                'month': month,
                'total_sys_tokens': total_sys,
                'total_input_tokens': total_in,
                'total_output_tokens': total_out,
                'session_count': sessions,
                'model_breakdown': json.dumps(model_data),
                'monthly_cap_mtokens': quest.monthly_cap_mtokens,
                'cap_exhausted_count': 1 if quest.cap_exhausted else 0,
            })
            created += 1

        _logger.info('Monthly summaries generated for %s: %d quests', month, created)
        return created


class AIQuestAgent(models.Model):
    _name = 'ai.quest.agent'
    _description = 'Quest Agent Assignment'
    _order = 'sequence asc'

    quest_id = fields.Many2one('ai.quest', required=True, ondelete='cascade')
    agent_id = fields.Many2one('ai.agent', required=True, string='Agent')
    sequence = fields.Integer(default=10)
