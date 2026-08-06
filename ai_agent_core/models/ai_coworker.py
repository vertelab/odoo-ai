# -*- coding: utf-8 -*-
"""ai.coworker — standalone, no LangGraph. Uses AgentLoop."""

import json, logging, re, uuid, base64
from odoo import models, fields, api, _
from odoo.exceptions import UserError
from odoo import SUPERUSER_ID

_logger = logging.getLogger(__name__)


def _quest_is_accessible(quest, user):
    """Check if a user can access a quest via the web chat."""
    if user.has_group('base.group_system'):
        return True
    if quest.user_id and quest.user_id.id == user.id:
        return True
    if quest.group_ids:
        user_grp = set(user.groups_id.ids)
        quest_grp = set(quest.group_ids.ids)
        if not (user_grp & quest_grp):
            return False
    return True


INIT_TYPES = [
    ('web_ui', 'Web Chat UI'),
    ('chat', 'Discuss — Private Chat'),
    ('channel', 'Discuss — Team Channel'),
    ('mail', 'Incoming Mail'),
    ('cron', 'Scheduled Action'),
    ('server_action', 'Server Action'),
    ('powerbox', 'Powerbox'),
    ('manual', 'Manual'),
    ('webhook', 'Webhook'),
    ('openai_api', 'OpenAI API'),
    ('watch', 'Watch — Dataändring'),
]

DEFAULT_AGENT_CREATOR_PROMPT = """You are a creative director designing AI agents for a Swedish workplace.
Create a memorable, slightly playful agent persona for the role: {topic}

Return ONLY a JSON object with these keys:
- name: a catchy Swedish-sounding name (e.g. "Moms-Magnus", "Bokslut-Britta")
- alias_name: short lowercase alias for @mentions (no spaces, e.g. "magnus")
- personality: 1-2 sentences describing character traits
- style: how the agent communicates
- values: guiding principles
- boundaries: what the agent must NOT do
- trigger_words: comma-separated keywords for routing
- avatar_description: visual description for an avatar

Keep it professional but with personality."""


class AICoworker(models.Model):
    _name = 'ai.coworker'
    _description = 'AI Quest'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'sequence asc, name asc'

    name = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    channel_alias = fields.Char(
        'Channel Alias (@mention)',
        help='Short name for @mentions in discuss channels. '
             'E.g. "redovisning" enables @redovisning in channels.',
        compute='_compute_channel_alias', inverse='_inverse_channel_alias',
        store=True)
    description = fields.Text(help='System prompt / quest purpose')
    sub_description = fields.Char('Short Description')
    active = fields.Boolean(default=True)
    color = fields.Integer(default=lambda self: __import__('random').randint(1, 11))

    status = fields.Selection([
        ('draft', 'Draft'), ('active', 'Active'), ('done', 'Done'), ('error', 'Error'),
    ], default='draft')

    init_type = fields.Selection(INIT_TYPES, string='Initiation Type (deprecated)',
        compute='_compute_init_type', store=True,
        help='Automatically synced from the first active Initiation Type. '
             'Use init_type_ids for multi-type support.')

    # ── Multi-model binding ──
    model_ids = fields.Many2many('ir.model', 'ai_coworker_model_rel',
        'coworker_id', 'model_id', string='Target Models',
        help='Models this quest can work with. For powerbox quests, '
             'the slash command only appears on records of these models.')
    model_id = fields.Many2one('ir.model', string='Target Model (primary)',
        compute='_compute_model_id', store=True, readonly=False,
        help='First model in the Target Models list. Deprecated in favor of model_ids.')
    model_name = fields.Char(compute='_compute_model_name', store=True)
    filter_domain = fields.Char('Record Filter')

    agent_ids = fields.One2many('ai.coworker.agent', 'coworker_id', string='Agents')
    agent_count = fields.Integer(compute='_compute_agent_count')
    is_supervisor = fields.Boolean(
        'Supervisor Mode', readonly=True,
        help='DEPRECATED (change ai-orchestration-tidy-up 6.5): använd '
             'orchestration_mode="supervisor" istället. Fältet synkas bakåt '
             'från orchestration_mode och är inte längre redigerbart.')

    orchestration_mode = fields.Selection([
        ('single', 'Single Agent'),
        ('supervisor', 'Supervisor (Hidden Team)'),
        ('buzz', 'Buzz Workspace (Visible Team)'),
        ('linear', 'Linear Pipeline'),
        ('conference', 'Conference (Best Answer)'),
        ('automation', 'Automation'),
    ], string='Orchestration Mode', default='single',
        help='How multiple agents collaborate in this quest. '
             'Buzz makes agents visible as channel members.')

    orchestration_mode_help = fields.Char(
        'Orchestration Mode — förklaring',
        compute='_compute_orchestration_mode_help', store=False, readonly=True,
        help='Kort beskrivning av vald orchestrationsläge (endast info).')

    supervisor_model_id = fields.Many2one(
        'ai.model',
        string='Supervisor Model',
        help='Modell som används av supervisorn/routern i Supervisor-, Buzz- '
             'och Conference-lägen. Lämnas tomt används första agentens modell.',
    )

    @api.depends('orchestration_mode')
    def _compute_orchestration_mode_help(self):
        help_text = {
            'single': 'En agent sköter allt — enkel assistent eller specialist.',
            'supervisor': 'En dold supervisor delegerar till specialister och '
                          'syntetiserar svaret (osynligt team).',
            'buzz': 'Synligt team i en Discuss-kanal — agenter med egna '
                    'identiteter som @nämns och samarbetar.',
            'linear': 'Sekventiell pipeline — varje agents svar blir nästa '
                      'agents prompt (sorterat på sequence).',
            'conference': 'Alla agenter får samma fråga — bästa svaret vinner '
                          '(majoritet, confidence eller syntes).',
            'automation': 'Schemalagd, headless exekvering utan mänsklig '
                          'interaktion (AUTO-permission).',
        }
        for rec in self:
            rec.orchestration_mode_help = help_text.get(
                rec.orchestration_mode, '')

    conference_mechanism = fields.Selection([
        ('confidence', 'Confidence (högst vinner)'),
        ('majority', 'Majoritet (röstning)'),
        ('synthesis', 'Syntes (LLM slår samman)'),
    ], string='Conference-mekanism', default='confidence',
        help='Hur konferensläget väljer bästa svar (change '
             'ai-orchestration-tidy-up 7.3). Kan överridas per-anrop via '
             'context-nyckeln conference_mechanism.')

    # ── Workspace Hermes injection level (D6) ──
    injection_level = fields.Selection([
        ('summary_only', 'Summary only (L3)'),
        ('summary_and_key', 'Summary + Key concepts (L3+L1)'),
        ('full', 'Full (L3+L1+L0)'),
    ], string='Hermes Injection Level', default='summary_and_key',
        help='How much of the OKF memory (L0-L3) is injected into the '
             'system prompt for this coworker. summary_only is cheapest and '
             'least noisy; full gives complete context at higher token cost.')

    # ── Minne (agent-memory-governance: runtime-saning) ──
    memory_scopes = fields.Many2many(
        'ai.memory.scope', 'ai_coworker_memory_scope_rel',
        'coworker_id', 'scope_id', string='Memory Scopes',
        help='Vilka minnen (company/personal/coworker) injiceras för denna '
             'AI Medarbetare. Seedas från identity vid skapande.')
    memory_level = fields.Selection([
        ('L0', 'L0 — Summary only'),
        ('L1', 'L1 — Summary + key'),
        ('L2', 'L2 — + strategi'),
        ('L3', 'L3 — Full'),
    ], string='Memory Level', default='L1',
        help='Omfattning per scope (ärvs av kopplingar utan eget värde).')
    memory_profile = fields.Selection([
        ('hermes', 'Hermes — lärande rådgivare'),
        ('balanced', 'Balanserad'),
        ('session_only', 'Session-only'),
    ], string='Memory Profile', default='balanced',
        help='Snabbstart: fyller i scopes/nivå. Identity-memory_profile seedar '
             'detta vid skapande.')
    learning = fields.Selection([
        ('active', 'Active — lär sig av samtal'),
        ('passive', 'Passive — injicerar bara, lär sig inte'),
    ], string='Learning', default='passive',
        help='Om medarbetaren skriver OKF-koncept från samtal (Hermes-lärande). ')


    # ── Buzz workspace settings ──
    allow_auto_create_agents = fields.Boolean(
        'Auto-create Agents', default=True,
        help='Allow this quest to proactively create new agents when needed.')
    max_auto_agents = fields.Integer(
        'Max Auto-created Agents', default=5,
        help='Hard limit on agents created automatically by this quest.')
    agent_creator_prompt = fields.Text(
        'Agent Creator Prompt',
        default=DEFAULT_AGENT_CREATOR_PROMPT,
        help='LLM prompt template for generating new agent personas.')

    # ── Buzz LLM routing settings ──
    buzz_use_llm_router = fields.Boolean(
        'Use LLM Router', default=False,
        help='Use AI to route messages in Buzz workspace (in addition to @mention and triggers).')
    buzz_a2a_max_depth = fields.Integer(
        'A2A Max Depth', default=3,
        help='Max agent-to-agent conversation depth in Buzz workspace.')

    # ── Supervisor settings ──
    max_iterations = fields.Integer(
        'Max Iterations', default=3,
        help='Maximum refinement rounds in supervisor task delegation.')
    min_confidence = fields.Float(
        'Min Confidence', default=0.8,
        help='Minimum confidence threshold for supervisor evaluation phase.')

    # ── Linear settings ──
    pass_full_history = fields.Boolean(
        'Pass Full History', default=False,
        help='Pass all previous agent outputs as context to next agent in linear pipeline.')

    # ── Multi-surface shadow session for Buzz workspaces ──
    buzz_channel_session_id = fields.Many2one(
        'ai.coworker.session', string='Buzz Channel Session',
        help='Shared web UI session that mirrors the linked Discuss channel.')

    # ── Orchestration helpers ──

    def _get_effective_orchestration_mode(self):
        """Return effective orchestration mode, honoring legacy is_supervisor."""
        self.ensure_one()
        if self.orchestration_mode and self.orchestration_mode != 'single':
            return self.orchestration_mode
        if self.is_supervisor:
            return 'supervisor'
        return 'single'

    def _ensure_orchestration_skill(self):
        """Se till att orchestration.supervisor-skillen är kopplad.

        Change ai-orchestration-tidy-up 6.1: skill-baserad supervisor är
        DEFAULT. Om coworkern är i supervisor-läge utan orchestration-skill
        kopplas standard-skillen automatiskt istället för att falla tillbaka
        till den hårdkodade SupervisorLoop-vägen.

        Returnerar recipe_text för den aktiva skillen ('' om ingen).
        """
        self.ensure_one()
        skill = self.skill_ids.filtered(
            lambda s: s.name and 'orchestration' in (s.name or '').lower())
        if skill:
            return skill[0].recipe_text or skill[0].improvement_guidance or ''
        default = self.env['ai.skill'].sudo().search(
            [('name', '=', 'orchestration.supervisor')], limit=1)
        if default:
            self.sudo().write({'skill_ids': [(4, default.id, 0)]})
            _logger.info(
                'Auto-kopplade orchestration.supervisor till coworker %s',
                self.name)
            return default.recipe_text or default.improvement_guidance or ''
        return ''

    # Kanban images (related for efficient kanban display)
    partner_image_128 = fields.Binary(related='partner_id.image_128',
                                       string='Partner Image',
                                       help='Avatar from channel partner')

    identity_id = fields.Many2one('ai.identity', string='Agent Identity',
        help='Select a template to create a personal copy for this quest. '
             'The copy evolves independently — template changes do NOT affect it.')

    # ── Identity auto-copy on selection ──

    @api.onchange('identity_id')
    def _onchange_identity_id(self):
        """When user selects a template identity, auto-create a copy.

        The copy lives independently — the quest's identity evolves
        separately from the original template. Same pattern as ai.coworker.skill.
        """
        if self.identity_id and self.identity_id.is_template:
            # Create a copy for this quest
            copy = self.identity_id.copy({
                'name': '%s — %s' % (self.identity_id.name, self.name or 'Quest'),
                'is_template': False,
                'template_id': self.identity_id.id,
                'scope': self.identity_id.scope,
            })
            self.identity_id = copy.id
            self._seed_memory_settings()
            return {
                'warning': {
                    'title': 'Identity kopierad',
                    'message': (
                        'En personlig kopia av "%s" har skapats för denna quest. '
                        'Kopian utvecklas oberoende av originalmallen.'
                    ) % self.identity_id.name,
                }
            }

    def _seed_memory_settings(self):
        """Seeda minnesinställningar från identity (agent-memory-governance 2.4).

        - memory_profile + memory_scopes + memory_level från identitetens
          memory_profile
        - kopplingarnas block från agenternas egna identiteter
        Idempotent: skriver bara tomma fält, inga duplikat.
        """
        self.ensure_one()
        identity = self.identity_id
        if not identity:
            return
        scope_model = self.env['ai.memory.scope']
        code_to_scope = {s.code: s for s in scope_model.search([])}

        # memory_profile från identity (om tom)
        if not self.memory_profile and identity.memory_profile:
            self.memory_profile = identity.memory_profile

        # learning från identity (om tom)
        if self.learning in (False, 'passive') and identity.learning == 'active':
            self.learning = 'active'

        # scopes från profilen
        if not self.memory_scopes:
            if identity.memory_profile == 'hermes':
                codes = ('company', 'personal', 'coworker')
                self.memory_level = self.memory_level or 'L1'
            elif identity.memory_profile == 'balanced':
                codes = ('company', 'coworker')
                self.memory_level = self.memory_level or 'L1'
            else:  # session_only
                codes = ()
                self.memory_level = self.memory_level or 'L0'
            self.memory_scopes = [(6, 0, [
                code_to_scope[c].id for c in codes if c in code_to_scope])]

        # kopplingarnas block från agentidentiteter (om tomma)
        for link in self.agent_ids:
            agent_identity = link.agent_id.identity_id
            if not agent_identity:
                continue
            if agent_identity.memory_profile == 'session_only':
                for scope in ('personal', 'company', 'coworker'):
                    if not getattr(link, 'block_%s' % scope):
                        link.write({'block_%s' % scope: True})
            elif agent_identity.memory_profile == 'balanced':
                if not link.block_personal:
                    link.block_personal = True

    cron_id = fields.Many2one('ir.cron', string='Scheduled Action',
        compute='_compute_init_type_fields', inverse='_inverse_init_type_fields',
        store=False, ondelete='cascade',
        help='Länkad ir.cron — auto-skapas av _ensure_cron när Cron är ikryssad.')
    cron_interval_number = fields.Integer('Interval', default=1)
    cron_interval_type = fields.Selection([
        ('minutes', 'Minutes'), ('hours', 'Hours'),
        ('days', 'Days'), ('weeks', 'Weeks'), ('months', 'Months'),
    ], default='hours', string='Interval Unit')
    # Domain-widget för cron-filter (mönster från ai.memory: model_id + model_name)
    cron_model_id = fields.Many2one('ir.model', string='Cron-modell',
        help='Modellen som cron-filter:et (filter_domain) byggs mot.')
    cron_model_name = fields.Char(
        related='cron_model_id.model', string='Cron Model Name',
        readonly=True, store=True)
    cron_automation_id = fields.Many2one(
        'ir.cron', string='Schedule Action', readonly=True,
        compute='_compute_init_type_fields', store=False,
        help='Auto-skapad ir.cron för Cron-initieringen (readonly).')
    server_action_automation_id = fields.Many2one(
        'ir.actions.server', string='Server Action (automation)', readonly=True,
        compute='_compute_init_type_fields', store=False,
        help='Auto-skapad ir.actions.server för Server Action-initieringen (readonly).')
    server_action_model_id = fields.Many2one('ir.model', string='Server Action-modell',
        help='Modellen som server action:en binder till (som cron_model_id).')
    server_action_model_name = fields.Char(
        related='server_action_model_id.model', string='Server Action Model Name',
        readonly=True, store=True)
    server_action_id = fields.Many2one('ir.actions.server', string='Server Action',
        compute='_compute_init_type_fields', inverse='_inverse_init_type_fields',
        store=False, ondelete='cascade',
        help='Länkad ir.actions.server — auto-skapas när Server Action är ikryssad.')
    server_action_use_wizard = fields.Boolean('Show Prompt Wizard', default=False)

    channel_id = fields.Many2one('discuss.channel', string='Channel')
    chat_user_id = fields.Many2one('res.users', string='Chat Bot User', readonly=True)
    allow_trigger_words = fields.Boolean('Use Activation Words')
    chat_trigger_words = fields.Text('Activation Words')

    response_mode = fields.Selection([
        ('always', 'Always Respond'),
        ('mention', 'Only on @mention'),
        ('trigger', 'Trigger words only'),
    ], default='mention', string='Response Mode')
    channel_reply_mode = fields.Selection([
        ('public', 'Public (in channel)'),
        ('private', 'Private (direct message)'),
        ('thread', 'Thread reply'),
    ], default='public', string='Reply Mode')

    use_chat_history = fields.Boolean(default=True)
    use_company_info = fields.Boolean(default=True)
    use_personal_info = fields.Boolean(default=True)
    use_personal_lang = fields.Boolean(default=True)

    # ── Company Memory Injection ──
    inject_company_memory = fields.Boolean(
        string='Inject Company Memory', default=False,
        help='Include the company\'s shared memories in the system prompt.')
    inject_nudging = fields.Boolean(
        string='Enable Nudging', default=False,
        help='Enable proactive nudges via chatter activities and notifications.')
    company_memory_categories = fields.Many2many(
        'ai.company.memory.category',
        'ai_coworker_company_memory_category_rel',
        'coworker_id', 'category_id',
        string='Company Memory Categories',
        help='Limit company memory to specific categories.\n'
             'Leave empty to include all accessible categories.')
    company_memory_artifact_types = fields.Many2many(
        'ai.artifact.type',
        'ai_coworker_company_memory_artifact_type_rel',
        'coworker_id', 'artifact_type_id',
        string='Company Memory Artifact Types (OKF)',
        help='Limit OKF company memory to specific artifact types.\n'
             'Leave empty to include all accessible types (task 7.2).')
    use_time_context = fields.Boolean(default=True)
    chat_history_limit = fields.Integer(default=10)

    # ── Context Injection (ported from ai_agent_context) ──
    context_injection_enabled = fields.Boolean('Enable Record Context', default=True)
    context_max_fields = fields.Integer('Max Context Fields', default=100)
    context_include_chatter = fields.Boolean('Include Chatter History', default=True)
    context_chatter_limit = fields.Integer('Chatter Message Limit', default=20)

    debug = fields.Boolean('Debug Mode')

    user_id = fields.Many2one('res.users', default=lambda self: self.env.user)
    company_id = fields.Many2one('res.company', default=lambda self: self.env.company)
    is_favorite = fields.Boolean('Favorite')

    # Access Control (quest-access-control)
    alias_name = fields.Char('Email Alias',
        compute='_compute_init_type_fields', inverse='_inverse_init_type_fields',
        store=False, help='Local part of the email address — synkas med mail-init:en')
    alias_display = fields.Char('Mailadress', compute='_compute_alias_display',
        store=False, help='Full mailadress: alias@företagets-mail-domän')
    # ── Mail trigger-settings (speglar mail-init:en) ──
    mail_action = fields.Selection([
        ('reply', 'Svara på mailet'),
        ('create_record', 'Skapa/uppdatera record'),
        ('invoice_ai', 'Leverantörsfaktura (AI)'),
    ], string='Mail-åtgärd', default='reply',
        compute='_compute_init_type_fields', inverse='_inverse_init_type_fields',
        store=False)
    mail_reply_delay = fields.Integer('Svarsdröjsmål (min)', default=0,
        compute='_compute_init_type_fields', inverse='_inverse_init_type_fields',
        store=False)
    mail_target_model_id = fields.Many2one('ir.model', string='Målmodell',
        compute='_compute_init_type_fields', inverse='_inverse_init_type_fields',
        store=False)
    mail_find_partner = fields.Boolean(
        'Hitta/skapa res.partner från avsändare', default=True,
        compute='_compute_init_type_fields', inverse='_inverse_init_type_fields',
        store=False)
    mail_invoice_agent_ids = fields.Many2many(
        'ai.agent', string='Faktura-agenter',
        compute='_compute_init_type_fields', inverse='_inverse_init_type_fields',
        store=False)
    mail_alias_ids = fields.One2many(
        'mail.alias', compute='_compute_mail_aliases', string='Mail-alias',
        store=False)

    @api.depends('alias_name')
    def _compute_alias_display(self):
        # Företagets MAIL-domän — i första hand alias-domänen på själva
        # mail.alias, sedan mail.catchall.domain → company.alias_domain_name
        # → domänen i företagets email (info@example.com → example.com).
        for rec in self:
            mail = rec.init_type_ids.filtered(
                lambda it: it.init_type == 'mail')[:1]
            alias = mail.alias_id if mail else False
            company = rec.company_id or self.env.company
            domain = (
                (alias.alias_domain if alias else False)
                or self.env['ir.config_parameter'].sudo().get_param(
                    'mail.catchall.domain')
                or company.alias_domain_name
                or (company.email or '').split('@')[-1] or ''
            )
            rec.alias_display = (
                f'{rec.alias_name}@{domain}'
                if rec.alias_name and domain else rec.alias_name or False)

    def _compute_mail_aliases(self):
        for rec in self:
            mail = rec.init_type_ids.filtered(
                lambda it: it.init_type == 'mail')[:1]
            rec.mail_alias_ids = mail.alias_id if mail else False

    def action_open_mail_alias(self):
        """Öppna mail-aliaset (mail.alias) för denna medarbetare."""
        self.ensure_one()
        mail = self.init_type_ids.filtered(
            lambda it: it.init_type == 'mail')[:1]
        if not mail or not mail.alias_id:
            return {
                'type': 'ir.actions.act_window_close',
            }
        return {
            'name': 'Mail-alias',
            'type': 'ir.actions.act_window',
            'res_model': 'mail.alias',
            'view_mode': 'form',
            'res_id': mail.alias_id.id,
            'target': 'new',
        }

    def action_test_mail_flow(self):
        """Simulera ett inkommande mail för att validera mail-förmågorna.

        Kör samma väg som mailgateway (message_new → mail_action-dispatch)
        med ett exempel-mail. Öppnar den skapade sessionen.
        """
        self.ensure_one()
        mail_it = self.init_type_ids.filtered(
            lambda it: it.init_type == 'mail' and it.enabled)[:1]
        if not mail_it or not mail_it.alias_id:
            raise UserError(
                'Mail-initieringen saknar aktiv alias. Sätt på Mail i '
                'Initiering och spara.')
        sample = {
            'reply': {
                'subject': 'TEST: Hej assistenten',
                'body': '<p>Hej! Kan du sammanfatta CRM-högen?</p>',
                'from': 'test@example.com',
            },
            'create_record': {
                'subject': 'TEST: Skapa kund',
                'body': '<p>Hej! Vänligen skapa en ny partner: '
                        'Acme Bygg AB, acme@bygg.se.</p>',
                'from': 'admin@example.com',
            },
            'invoice_ai': {
                'subject': 'TEST: Faktura 2026-001',
                'body': '<p>Hej! Här är en testfaktura från Acme Bygg AB, '
                        'totalt 12 400 kr inkl moms.</p>',
                'from': 'billing@acme-bygg.se',
            },
        }
        sample_msg = sample.get(mail_it.mail_action or 'reply', sample['reply'])
        msg = {
            'subject': sample_msg['subject'],
            'body': sample_msg['body'],
            'from': sample_msg['from'],
            'attachment_ids': [],
        }
        session = self.env['ai.coworker.session'].with_context(
            mail_create_nosubscribe=True).message_new(
                msg, {'coworker_id': self.id})
        return {
            'type': 'ir.actions.client_notification',
            'title': 'Mail-test klart',
            'message': (
                f'Simulerat mail → session {session.id} ({self.name}).\n'
                'Öppna Odoo Mind → Sessions för att se resultatet.'
            ),
            'sticky': False,
        }
    group_ids = fields.Many2many('res.groups', 'ai_coworker_group_rel', 'coworker_id', 'group_id', string='Access Groups')

    # Core loop migration
    use_core_loop = fields.Boolean('Use Core Loop', default=False)

    session_count = fields.Integer(compute='_compute_session_count')
    session_ids = fields.One2many('ai.coworker.session', 'coworker_id')
    session_object_count = fields.Integer(compute='_compute_session_object_count')

    @api.depends('session_ids')
    def _compute_session_object_count(self):
        for r in self:
            if 'ai.session.object' in self.env:
                r.session_object_count = self.env['ai.session.object'].search_count(
                    [('ai_coworker_id', '=', r.id)])
            else:
                r.session_object_count = 0

    # Systemtoken tracking
    session_line_ids = fields.One2many(
        'ai.coworker.session.line', related='session_ids.session_line_ids',
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

    # ── Autonomi-panel (gap C5/F4, task 8.6) ──
    budget_kr_monthly = fields.Float(
        'Budget (kr/mån)', default=0.0,
        help='Autonomi-budget i kronor per månad. 0 = ingen explicit '
             'kr-budget (endast mtokens-tak). Hårt stopp när budgeten är slut.')
    max_actions_per_day = fields.Integer(
        'Max åtgärder/dag', default=50,
        help='Hårt stopp: antal express-actions per dag innan coworkern '
             'måste vänta till nästa dygn.')
    hitl_threshold = fields.Selection([
        ('autonomous', 'Autonom (inga godkännanden)'),
        ('high_risk', 'Endast högriskåtgärder kräver godkännande'),
        ('always', 'Alltid godkännande (HITL)'),
    ], string='HITL-tröskel', default='high_risk',
        help='När krävs godkännande innan coworkern agerar. '
             'Paperclip-approval-gates som default: högriskåtgärder '
             '(skrivningar, externa anrop) kräver alltid HITL.')

    # ── Coworker-katalog (gap A1/A3, task 8.7) ──
    example_prompts = fields.Text(
        'Exempel-prompts',
        help='Exempel på hur användare kan be denna coworker om hjälp. '
             'Visas i coworker-katalogen (per roll).')

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

    skill_copy_ids = fields.One2many('ai.coworker.skill', 'coworker_id',
        string='Skill Copies',
        help='Quest-specific copies of shared skills')

    # ── Direct quest-level skills (pipeline/orchestration) ──
    skill_ids = fields.Many2many('ai.skill', 'ai_coworker_skill_rel',
        'coworker_id', 'skill_id', string='Quest Skills',
        help='Pipeline and orchestration skills. '
             'Available to ALL agents in this quest. '
             'These provide the overall coordination framework '
             'and take priority over agent-level skills.')

    # ── Coworker EGNA tools (D5) ──
    tool_ids = fields.Many2many('ai.tool', 'ai_coworker_tool_custom_rel',
        'coworker_id', 'tool_id', string='Tools',
        help='Custom ai.tool records available to this coworker in run(). '
             'Linked to coworker_ids on ai.tool.')

    # Serialiseringsläge för förmågor (ai-tool-access-capabilities):
    # flat = individuella verktyg (default, dagens beteende); enum = en Tool
    # per förmåga med operation-enum; namespace = individuella verktyg +
    # förmågans beskrivning i systemprompten. Separat från access (group_ids).
    serialize_capabilities = fields.Selection([
        ('flat', 'Flat (individuella verktyg)'),
        ('enum', 'Enum (förmåga → ett verktyg med operation-enum)'),
        ('namespace', 'Namespace (verktyg + förmågans beskrivning)'),
    ], default='flat', string='Serialize Capabilities',
        help='Hur ai.tool.capability-förmågor serialiseras till LLM:en. '
             'enum = minimal kontext (bra för små modeller); namespace = '
             'parallellitet + samlad beskrivning. Access (group_ids) gäller '
             'oavsett — filtrering sker före serialisering.')
    last_run = fields.Datetime()

    # ── Automation / Scheduled Run (OpenWorker-inspired) ──
    last_status = fields.Selection([
        ('ok', 'OK'), ('error', 'Error'), ('skipped', 'Skipped'),
    ], string='Last Run Status',
       help='Result of the last scheduled run')
    run_count = fields.Integer('Run Count', default=0,
                                help='Number of times this quest has been run via cron')
    notify_on_completion = fields.Boolean('Notify on Completion',
                                           help='Send a notification when a scheduled run completes')
    notify_target = fields.Char('Notify Target',
                                 help='Channel or user reference for completion notifications')
    auto_allowed_tools = fields.Text('Auto-Allowed Tools',
                                      help='JSON list of tool→target standing rules. '
                                           'Format: ["tool target", ...] or bare tool names. '
                                           'Applied as auto-approve for scheduled runs.')
    auto_allowed_commands = fields.Text('Auto-Allowed Commands',
                                         help='JSON list of command prefixes. '
                                              'Commands matching these prefixes are auto-approved '
                                              'for scheduled runs without prompting.')

    tag_ids = fields.Many2many('ai.tag', string='Tags')

    # ── Multi-init-type (replaces single init_type) ──
    init_type_ids = fields.One2many('ai.coworker.init_type', 'coworker_id',
        string='Initiation Types',
        help='Multiple ways this quest can be triggered.')
    # Computed Many2many for many2many_tags widget in form
    active_init_types = fields.Many2many(
        'ai.coworker.init_type', 'ai_coworker_init_type_active_rel',
        'coworker_id', 'init_type_id',
        string='Active Initiation Types',
        compute='_compute_active_init_types', inverse='_inverse_active_init_types',
        help='Select which ways this quest can be triggered. '
             'Each type lights up its own configuration below.')

    # Computed boolean flags for UI visibility
    has_web_ui = fields.Boolean(compute='_compute_init_type_flags', store=False)
    has_chat = fields.Boolean(compute='_compute_init_type_flags', store=False)
    has_channel = fields.Boolean(compute='_compute_init_type_flags', store=False)
    has_mail = fields.Boolean(compute='_compute_init_type_flags', store=False)
    has_cron = fields.Boolean(compute='_compute_init_type_flags', store=False)
    has_server_action = fields.Boolean(compute='_compute_init_type_flags', store=False)
    has_powerbox = fields.Boolean(compute='_compute_init_type_flags', store=False)
    has_controller = fields.Boolean(compute='_compute_init_type_flags', store=False)
    has_openai_api = fields.Boolean(compute='_compute_init_type_flags', store=False)
    has_webhook = fields.Boolean(compute='_compute_init_type_flags', store=False)
    has_watch = fields.Boolean(compute='_compute_init_type_flags', store=False)
    show_in_chat = fields.Boolean(
        'Visa i Web Chat', default=True,
        compute='_compute_init_type_fields', inverse='_inverse_init_type_fields',
        store=False,
        help='Visas i /ai/chat (init_type web_ui).')

    # ── Webhook config ──
    webhook_secret = fields.Char('Webhook Secret',
        readonly=True,
        help='Shared secret for webhook init_type. Auto-generated. '
             'External systems send this in Authorization: Bearer header')
    max_webhook_payload_size = fields.Integer('Max Payload Size (bytes)',
        default=1048576,
        help='Maximum allowed payload size for webhook requests (default 1MB)')
    webhook_url = fields.Char('Webhook URL',
        compute='_compute_webhook_url', store=False, readonly=True,
        help='URL som externa system (t.ex. Zabbix) POST:ar till.')

    # ── Per-typ-konfiguration genom aktiva init_type-rader (watch, mail,
    #    openai_api) — computed + inverse skriver tillbaka till raden. ──
    watch_model_id = fields.Many2one(
        'ir.model', string='Watch Model',
        compute='_compute_init_type_fields', inverse='_inverse_init_type_fields',
        store=False, help='Model att bevaka för dataändringar.')
    watch_model_name = fields.Char(
        related='watch_model_id.model', string='Watch Model Name',
        readonly=True, store=False)
    # Speglar base_automation.trigger — samma värden.
    watch_trigger = fields.Selection([
        ('on_stage_set', 'Stage is set to'),
        ('on_user_set', 'User is set'),
        ('on_tag_set', 'Tag is added'),
        ('on_state_set', 'State is set to'),
        ('on_priority_set', 'Priority is set to'),
        ('on_archive', 'On archived'),
        ('on_unarchive', 'On unarchived'),
        ('on_create_or_write', 'On save'),
        ('on_create', 'On creation'),
        ('on_write', 'On update'),
        ('on_unlink', 'On deletion'),
        ('on_change', 'On UI change'),
        ('on_time', 'Based on date field'),
        ('on_time_created', 'After creation'),
        ('on_time_updated', 'After last update'),
        ('on_webhook', 'On webhook'),
    ], string='When updating', default='on_create_or_write',
        compute='_compute_init_type_fields', inverse='_inverse_init_type_fields',
        store=False, help='Vilken händelse väcker medarbetaren.')
    watch_trg_selection_field_id = fields.Many2one(
        'ir.model.fields.selection', string='Trigger Field',
        compute='_compute_init_type_fields', inverse='_inverse_init_type_fields',
        store=False, help='Selection-fält för on_state_set/on_priority_set.')
    watch_trg_field_ref_model_name = fields.Char(
        string='Trigger Field Model',
        compute='_compute_init_type_fields', store=False,
        readonly=True)
    watch_trg_field_ref = fields.Many2oneReference(
        string='Trigger Reference',
        compute='_compute_init_type_fields', inverse='_inverse_init_type_fields',
        store=False, help='Fält-ref för on_stage_set/on_tag_set.')
    watch_trg_date_id = fields.Many2one(
        'ir.model.fields', string='Trigger Date',
        compute='_compute_init_type_fields', inverse='_inverse_init_type_fields',
        store=False, help='Datumfält för on_time-trigger.')
    watch_trg_date_range = fields.Integer(
        string='Delay after trigger date',
        compute='_compute_init_type_fields', inverse='_inverse_init_type_fields',
        store=False)
    watch_trg_date_range_type = fields.Selection([
        ('minutes', 'Minutes'),
        ('hour', 'Hours'),
        ('day', 'Days'),
        ('month', 'Months'),
    ], string='Delay type',
        compute='_compute_init_type_fields', inverse='_inverse_init_type_fields',
        store=False)
    watch_trg_date_calendar_id = fields.Many2one(
        'resource.calendar', string='Use Calendar',
        compute='_compute_init_type_fields', inverse='_inverse_init_type_fields',
        store=False)
    watch_filter_pre_domain = fields.Char(
        string='Before Update Domain',
        compute='_compute_init_type_fields', inverse='_inverse_init_type_fields',
        store=False)
    watch_filter_domain = fields.Char(
        string='Apply on',
        compute='_compute_init_type_fields', inverse='_inverse_init_type_fields',
        store=False)
    watch_trigger_field_ids = fields.Many2many(
        'ir.model.fields', string='When updating',
        compute='_compute_init_type_fields', inverse='_inverse_init_type_fields',
        store=False, help='Fält som bevakas — tomt = alla fält.')
    watch_on_change_field_ids = fields.Many2many(
        'ir.model.fields', string='When updating (on change)',
        compute='_compute_init_type_fields', inverse='_inverse_init_type_fields',
        store=False)
    watch_active = fields.Boolean(
        string='Active',
        compute='_compute_init_type_fields', inverse='_inverse_init_type_fields',
        store=False, help='Avbockad = regeln göms och körs inte.')
    watch_domain = fields.Char('Watch Domain',
        compute='_compute_init_type_fields', inverse='_inverse_init_type_fields',
        store=False, help='Domänfilter för vilka records som triggar, '
             't.ex. [("priority", ">", 5)]')
    base_automation_id = fields.Many2one(
        'base.automation', string='Base Automation', readonly=True,
        compute='_compute_init_type_fields', store=False,
        help='Auto-skapad base.automation (länk).')
    alias_contact = fields.Selection([
        ('everyone', 'Everyone'),
        ('partners', 'Authenticated Partners'),
        ('followers', 'Followers only'),
        ('employees', 'Authenticated Employees'),
    ], default='everyone', string='Accept Emails From',
        compute='_compute_init_type_fields', inverse='_inverse_init_type_fields',
        store=False)
    rate_limit_rpm = fields.Integer('Rate Limit (req/min)', default=30,
        compute='_compute_init_type_fields', inverse='_inverse_init_type_fields',
        store=False)
    rate_limit_tpm = fields.Integer('Rate Limit (tokens/min)', default=100000,
        compute='_compute_init_type_fields', inverse='_inverse_init_type_fields',
        store=False)

    def _get_active_init(self, itype):
        """Returnera den aktiva init_type-raden för en typ (eller tom recordset)."""
        return self.init_type_ids.filtered(
            lambda it: it.init_type == itype and it.enabled)[:1]

    # ── Initiering: en Boolean per typ (kryssruta). Vanliga Boolean-fält
    #    gör att invisible på konfig-fälten fungerar direkt i onchange —
    #    mycket pålitligare än many2many_checkboxes + computed has_*.
    init_web_ui = fields.Boolean('Web Chat UI',
        compute='_compute_init_booleans', inverse='_inverse_init_booleans')
    init_chat = fields.Boolean('Discuss — Private Chat',
        compute='_compute_init_booleans', inverse='_inverse_init_booleans')
    init_channel = fields.Boolean('Discuss — Team Channel',
        compute='_compute_init_booleans', inverse='_inverse_init_booleans')
    init_mail = fields.Boolean('Incoming Mail',
        compute='_compute_init_booleans', inverse='_inverse_init_booleans')
    init_cron = fields.Boolean('Scheduled Action',
        compute='_compute_init_booleans', inverse='_inverse_init_booleans')
    init_server_action = fields.Boolean('Server Action',
        compute='_compute_init_booleans', inverse='_inverse_init_booleans')
    init_powerbox = fields.Boolean('Powerbox',
        compute='_compute_init_booleans', inverse='_inverse_init_booleans')
    init_manual = fields.Boolean('Manual',
        compute='_compute_init_booleans', inverse='_inverse_init_booleans')
    init_webhook = fields.Boolean('Webhook',
        compute='_compute_init_booleans', inverse='_inverse_init_booleans')
    init_openai_api = fields.Boolean('OpenAI API',
        compute='_compute_init_booleans', inverse='_inverse_init_booleans')
    init_watch = fields.Boolean('Watch — Dataändring',
        compute='_compute_init_booleans', inverse='_inverse_init_booleans')

    _INIT_BOOLEAN_MAP = [
        ('init_web_ui', 'web_ui'), ('init_chat', 'chat'),
        ('init_channel', 'channel'), ('init_mail', 'mail'),
        ('init_cron', 'cron'), ('init_server_action', 'server_action'),
        ('init_powerbox', 'powerbox'), ('init_manual', 'manual'),
        ('init_webhook', 'webhook'), ('init_openai_api', 'openai_api'),
        ('init_watch', 'watch'),
    ]

    @api.depends('init_type_ids', 'init_type_ids.enabled', 'init_type_ids.init_type')
    def _compute_init_booleans(self):
        for rec in self:
            enabled = set(
                it.init_type for it in rec.init_type_ids if it.enabled)
            for field_name, itype in self._INIT_BOOLEAN_MAP:
                rec[field_name] = itype in enabled

    def _inverse_init_booleans(self):
        for rec in self:
            for field_name, itype in self._INIT_BOOLEAN_MAP:
                rec._set_init_type(itype, bool(rec[field_name]))

    def _set_init_type(self, itype, enabled):
        """Aktivera/deaktivera en init_type-rad; skapa raden om den saknas."""
        rec = self._get_active_init(itype)
        if enabled and not rec:
            row = self.init_type_ids.filtered(
                lambda it: it.init_type == itype)[:1]
            if row:
                row.enabled = True
            else:
                row = self.env['ai.coworker.init_type'].create({
                    'coworker_id': self.id,
                    'init_type': itype,
                    'enabled': True,
                })
        elif not enabled and rec:
            rec.enabled = False

    @api.depends('init_type_ids', 'init_type_ids.enabled',
                 'init_type_ids.init_type', 'init_type_ids.watch_model_id',
                 'init_type_ids.watch_trigger', 'init_type_ids.watch_domain',
                 'init_type_ids.base_automation_id', 'init_type_ids.alias_contact',
                 'init_type_ids.alias_name', 'init_type_ids.alias_id.alias_name',
                 'init_type_ids.mail_action',
                 'init_type_ids.mail_reply_delay',
                 'init_type_ids.mail_target_model_id',
                 'init_type_ids.mail_find_partner',
                 'init_type_ids.mail_invoice_agent_ids',
                 'init_type_ids.rate_limit_rpm', 'init_type_ids.rate_limit_tpm',
                 'init_type_ids.show_in_chat', 'init_type_ids.cron_id',
                 'init_type_ids.server_action_id',
                 'init_type_ids.watch_trg_selection_field_id',
                 'init_type_ids.watch_trg_field_ref',
                 'init_type_ids.watch_trg_field_ref_model_name',
                 'init_type_ids.watch_trg_date_id',
                 'init_type_ids.watch_trg_date_range',
                 'init_type_ids.watch_trg_date_range_type',
                 'init_type_ids.watch_trg_date_calendar_id',
                 'init_type_ids.watch_filter_pre_domain',
                 'init_type_ids.watch_filter_domain',
                 'init_type_ids.watch_trigger_field_ids',
                 'init_type_ids.watch_on_change_field_ids',
                 'init_type_ids.watch_active')
    def _compute_init_type_fields(self):
        for rec in self:
            watch = rec._get_active_init('watch')
            rec.watch_model_id = watch.watch_model_id if watch else False
            rec.watch_trigger = (watch.watch_trigger
                                 if watch else 'on_create_or_write')
            rec.watch_domain = watch.watch_domain if watch else False
            rec.watch_trg_selection_field_id = (
                watch.watch_trg_selection_field_id if watch else False)
            rec.watch_trg_field_ref = watch.watch_trg_field_ref if watch else False
            rec.watch_trg_field_ref_model_name = (
                watch.watch_trg_field_ref_model_name if watch else False)
            rec.watch_trg_date_id = watch.watch_trg_date_id if watch else False
            rec.watch_trg_date_range = (watch.watch_trg_date_range
                                        if watch else False)
            rec.watch_trg_date_range_type = (watch.watch_trg_date_range_type
                                             if watch else False)
            rec.watch_trg_date_calendar_id = (watch.watch_trg_date_calendar_id
                                              if watch else False)
            rec.watch_filter_pre_domain = (watch.watch_filter_pre_domain
                                           if watch else False)
            rec.watch_filter_domain = watch.watch_filter_domain if watch else False
            rec.watch_trigger_field_ids = (watch.watch_trigger_field_ids
                                           if watch else False)
            rec.watch_on_change_field_ids = (watch.watch_on_change_field_ids
                                             if watch else False)
            rec.watch_active = watch.watch_active if watch else True
            # Länkade resurser visas oavsett enabled-status (rader kan finnas
            # även om init-typen är avstängd).
            watch_any = rec.init_type_ids.filtered(
                lambda it: it.init_type == 'watch')[:1]
            rec.base_automation_id = (watch_any.base_automation_id
                                      if watch_any else False)
            cron_any = rec.init_type_ids.filtered(
                lambda it: it.init_type == 'cron')[:1]
            rec.cron_id = cron_any.cron_id if cron_any else False
            rec.cron_automation_id = cron_any.cron_id if cron_any else False
            sa_any = rec.init_type_ids.filtered(
                lambda it: it.init_type == 'server_action')[:1]
            rec.server_action_id = sa_any.server_action_id if sa_any else False
            rec.server_action_automation_id = (sa_any.server_action_id
                                               if sa_any else False)
            mail = rec._get_active_init('mail')
            rec.alias_contact = mail.alias_contact if mail else 'everyone'
            rec.alias_name = False
            if mail:
                rec.alias_name = (mail.alias_name
                                  or (mail.alias_id.alias_name
                                      if mail.alias_id else False)
                                  or False)
            rec.mail_action = mail.mail_action if mail else 'reply'
            rec.mail_reply_delay = mail.mail_reply_delay if mail else 0
            rec.mail_target_model_id = (mail.mail_target_model_id
                                        if mail else False)
            rec.mail_find_partner = mail.mail_find_partner if mail else True
            rec.mail_invoice_agent_ids = (mail.mail_invoice_agent_ids
                                          if mail else False)
            oa = rec._get_active_init('openai_api')
            rec.rate_limit_rpm = oa.rate_limit_rpm if oa else 30
            rec.rate_limit_tpm = oa.rate_limit_tpm if oa else 100000
            webui = rec._get_active_init('web_ui')
            rec.show_in_chat = webui.show_in_chat if webui else True

    def _inverse_init_type_fields(self):
        for rec in self:
            watch = rec._get_active_init('watch')
            if watch:
                watch.write({
                    'watch_model_id': rec.watch_model_id.id if rec.watch_model_id else False,
                    'watch_trigger': rec.watch_trigger,
                    'watch_domain': rec.watch_domain,
                    'watch_trg_selection_field_id': rec.watch_trg_selection_field_id.id if rec.watch_trg_selection_field_id else False,
                    'watch_trg_field_ref': rec.watch_trg_field_ref.id if rec.watch_trg_field_ref else False,
                    'watch_trg_date_id': rec.watch_trg_date_id.id if rec.watch_trg_date_id else False,
                    'watch_trg_date_range': rec.watch_trg_date_range,
                    'watch_trg_date_range_type': rec.watch_trg_date_range_type,
                    'watch_trg_date_calendar_id': rec.watch_trg_date_calendar_id.id if rec.watch_trg_date_calendar_id else False,
                    'watch_filter_pre_domain': rec.watch_filter_pre_domain,
                    'watch_filter_domain': rec.watch_filter_domain,
                    'watch_trigger_field_ids': [(6, 0, rec.watch_trigger_field_ids.ids)] if rec.watch_trigger_field_ids else [(5, 0, 0)],
                    'watch_on_change_field_ids': [(6, 0, rec.watch_on_change_field_ids.ids)] if rec.watch_on_change_field_ids else [(5, 0, 0)],
                    'watch_active': rec.watch_active,
                })
                # Skapa/uppdatera base_automation direkt när modellen ändras
                if watch.enabled and rec.watch_model_id:
                    watch._ensure_watch()
            cron = rec._get_active_init('cron')
            if cron:
                cron.cron_id = rec.cron_id
            sa = rec._get_active_init('server_action')
            if sa:
                sa.server_action_id = rec.server_action_id
                # Skapa/uppdatera server action direkt när modellen ändras
                if sa.enabled and (rec.server_action_model_id or rec.model_ids):
                    sa._ensure_server_action()
            mail = rec._get_active_init('mail')
            if mail:
                mail.alias_contact = rec.alias_contact
                mail.mail_action = rec.mail_action
                mail.mail_reply_delay = rec.mail_reply_delay
                mail.mail_target_model_id = (rec.mail_target_model_id.id
                                             if rec.mail_target_model_id else False)
                mail.mail_find_partner = rec.mail_find_partner
                mail.mail_invoice_agent_ids = [
                    (6, 0, rec.mail_invoice_agent_ids.ids)]
                if rec.alias_name:
                    mail.alias_name = rec.alias_name
                    if mail.enabled:
                        mail._ensure_mail_alias()
            oa = rec._get_active_init('openai_api')
            if oa:
                oa.write({
                    'rate_limit_rpm': rec.rate_limit_rpm,
                    'rate_limit_tpm': rec.rate_limit_tpm,
                })
            webui = rec._get_active_init('web_ui')
            if webui:
                webui.show_in_chat = rec.show_in_chat

    @api.depends()
    def _compute_webhook_url(self):
        for rec in self:
            rec.webhook_url = f'/ai/webhook/{rec.id}' if rec.id else ''

    @api.depends('init_type_ids', 'init_type_ids.enabled', 'init_type_ids.init_type')
    def _compute_active_init_types(self):
        for r in self:
            r.active_init_types = r.init_type_ids.filtered('enabled')

    def _inverse_active_init_types(self):
        for r in self:
            # Get currently active init types
            current_map = {it.init_type: it for it in r.init_type_ids}
            wanted_types = set(it.init_type for it in r.active_init_types)
            current_types = set(current_map.keys())

            # CREATE new init types that don't exist yet
            to_create = wanted_types - current_types
            for itype in to_create:
                self.env['ai.coworker.init_type'].create({
                    'coworker_id': r.id,
                    'init_type': itype,
                    'enabled': True,
                })

            # ACTIVATE existing that should be active
            to_activate = wanted_types & current_types
            for itype in to_activate:
                if itype in current_map and not current_map[itype].enabled:
                    current_map[itype].enabled = True

            # DEACTIVATE existing that should not be active
            to_deactivate = current_types - wanted_types
            for itype in to_deactivate:
                if itype in current_map and current_map[itype].enabled:
                    current_map[itype].enabled = False

    @api.onchange('active_init_types')
    def _onchange_active_init_types(self):
        """Recomputa has_*-flaggorna direkt när användaren kryssar en typ,
        så att Typinställningarna för den typen visas utan sidladdning."""
        if self:
            self._compute_init_type_flags()

    @api.depends('init_type_ids.init_type', 'init_type_ids.enabled')
    def _compute_init_type_flags(self):
        for r in self:
            active_types = set(
                it.init_type for it in r.init_type_ids if it.enabled
            )
            r.has_web_ui = 'web_ui' in active_types
            r.has_chat = 'chat' in active_types
            r.has_channel = 'channel' in active_types
            r.has_mail = 'mail' in active_types
            r.has_cron = 'cron' in active_types
            r.has_server_action = 'server_action' in active_types
            r.has_powerbox = 'powerbox' in active_types
            r.has_controller = 'controller' in active_types
            r.has_webhook = 'webhook' in active_types
            r.has_watch = 'watch' in active_types
            r.has_openai_api = 'openai_api' in active_types

    @api.depends('model_ids')
    def _compute_model_id(self):
        for r in self:
            r.model_id = r.model_ids[0] if r.model_ids else False

    @api.depends('model_id.model')
    def _compute_model_name(self):
        for r in self:
            r.model_name = r.model_id.model if r.model_id else False

    @api.depends('name')
    def _compute_channel_alias(self):
        """Default channel_alias from name (lowercase, no spaces)."""
        import re
        for r in self:
            if not r.name:
                r.channel_alias = ''
            elif not r.channel_alias:
                r.channel_alias = re.sub(r'[^a-z0-9]', '', r.name.lower()[:20])

    def _inverse_channel_alias(self):
        """Allow manual override of channel_alias."""
        pass  # Value is stored directly

    @api.depends('init_type_ids', 'init_type_ids.enabled')
    def _compute_init_type(self):
        """Keep legacy init_type in sync: first enabled init type."""
        for r in self:
            active = r.init_type_ids.filtered('enabled')
            r.init_type = active[0].init_type if active else 'manual'

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
            summary = self.env['ai.coworker.monthly_summary'].search([
                ('coworker_id', '=', r.id),
                ('month', '=', last_month),
            ], limit=1)
            r.last_month_sys_tokens = summary.total_sys_tokens if summary else 0

    def _get_eid(self):
        """Get external ID for this quest, creating one if needed."""
        self.ensure_one()
        if not self.name:
            from odoo.exceptions import ValidationError
            raise ValidationError(_('Set a name for this quest'))
        eid = list(self.get_external_id().values())[0]
        if not eid:
            import re
            import unidecode
            eid_name = unidecode.unidecode(re.sub(
                r'[^a-zA-Z0-9\s]', '', self.name.lower()
            ).replace(' ', '_'))
            if self.id:
                eid_name += f"_{self.id}"
            eid = self.env['ir.model.data'].search(
                [('name', '=', eid_name)], limit=1)
            if not eid:
                eid = self.env['ir.model.data'].create({
                    'name': eid_name,
                    'module': 'ai_agent_core',
                    'model': 'ai.coworker',
                    'res_id': self.id,
                })
                # eid är nu en record → bygg xmlid
                return eid.complete_name or '%s.%s' % (eid.module, eid.name)
        return eid  # eid är redan en xmlid-sträng från get_external_id()

    def server_action(self, records):
        """Run quest as a server action triggered from Odoo UI.
        
        Called from ir.actions.server when user clicks the quest button
        on a form or selects records in a list.
        """
        self.ensure_one()
        if self._check_quest_error():
            _logger.error('Server Action error: %s', self._check_quest_error())
            raise UserError(self._check_quest_error())
        
        record = records[0] if records else None
        result = self.run(records=records, record=record)
        return result

    # ── Mail (init_type='mail') ──

    def mail(self, mail_message, session=None):
        """Handle incoming mail → create session and run AgentLoop."""
        self.ensure_one()
        if self._check_quest_error():
            _logger.warning('Mail quest %s has errors, skipping', self.name)
            return None

        from odoo.tools.mail import html2plaintext
        try:
            body_text = html2plaintext(mail_message.body or '')
        except Exception:
            body_text = mail_message.body or ''

        if not body_text.strip():
            return None

        if session is None:
            session = self.env['ai.coworker.session'].create({
                'coworker_id': self.id, 'status': 'active',
                'name': f'Mail: {mail_message.subject or "No subject"}',
                'user_id': self.env.ref('base.user_root', raise_if_not_found=False).id or 1,
            })

        # Handle mail attachments as context
        att_context = ''
        if mail_message.attachment_ids:
            att_texts = []
            for att in mail_message.attachment_ids:
                try:
                    text = _extract_text(att.name, base64.b64decode(att.datas))
                    if text and len(text) < 10000:
                        att_texts.append(f'--- {att.name} ---\n{text[:2000]}')
                except Exception:
                    pass
            if att_texts:
                att_context = '\n\n## Attachments\n' + '\n'.join(att_texts)

        prompt = body_text[:4000] + att_context
        try:
            result = self.run(session=session, message_body=prompt, mail=mail_message, prompt=prompt)
            if result and session:
                last_line = session.session_line_ids.sorted('sequence', reverse=True)[:1]
                if last_line and last_line.role == 'assistant' and last_line.content:
                    _send_mail_reply(mail_message, last_line.content[:4000], self)
            return result
        except Exception as e:
            _logger.error('Mail failed for quest %s: %s', self.name, e)
            if session:
                session.write({'status': 'error', 'finish_reason': str(e)[:200]})
            return None

    # ── Chat / Channel (init_type='chat' | 'channel') ──

    def chat(self, message, channel=None, bot_user=None):
        """Handle Discuss message → run AgentLoop and respond."""
        self.ensure_one()
        if self._check_quest_error():
            return None

        # Response mode check (backward compat — _route_message() already filters)
        active_init = self.init_type_ids.filtered(
            lambda it: it.init_type in ('chat', 'channel') and it.enabled)
        if active_init and active_init[0].response_mode == 'trigger':
            trigger_words = active_init[0].chat_trigger_words or ''
            if trigger_words:
                msg_lower = (message.body or '').lower()
                triggers = [w.strip().lower() for w in trigger_words.split(',')]
                if not any(t in msg_lower for t in triggers):
                    return None

        # Chat history context
        history_ctx = ''
        use_hist = active_init and active_init[0].use_chat_history
        hist_limit = active_init[0].chat_history_limit if active_init else 10
        if use_hist and channel:
            prev = self.env['mail.message'].search([
                ('model', '=', 'discuss.channel'), ('res_id', '=', channel.id),
            ], limit=hist_limit, order='create_date asc')
            lines = []
            for m in prev:
                role = 'assistant' if m.author_id == bot_user else 'user'
                text = (m.body or '')[:500]
                if text.strip():
                    lines.append(f'[{role}] {text}')
            if lines:
                history_ctx = '\n'.join(lines[-hist_limit:])

        from odoo.tools.mail import html2plaintext
        try:
            msg_text = html2plaintext(message.body or '')
        except Exception:
            msg_text = message.body or ''

        if not msg_text.strip():
            return None

        # ── Buzz workspace branch ──
        if self._get_effective_orchestration_mode() == 'buzz':
            return self._buzz_chat(message, channel, msg_text, history_ctx)

        full_system = (self.description or '')
        if history_ctx:
            full_system += f'\n\n## Recent conversation\n{history_ctx}'

        session = self.env['ai.coworker.session'].create({
            'coworker_id': self.id, 'status': 'active',
            'name': f'Chat: {msg_text[:50]}',
            'user_id': bot_user.id if bot_user else 1,
        })
        self.env['ai.coworker.session.line'].create({
            'session_id': session.id, 'sequence': 1,
            'role': 'user', 'content': msg_text[:4000],
        })

        try:
            result = self.run(session=session, message=message,
                            message_body=msg_text, prompt=msg_text,
                            channel=channel, bot_user=bot_user)
            if session and channel:
                last_line = session.session_line_ids.sorted('sequence', reverse=True)[:1]
                if last_line and last_line.role == 'assistant' and last_line.content:
                    channel.message_post(
                        body=f'<p>{last_line.content[:4000]}</p>',
                        message_type='comment', subtype_xmlid='mail.mt_comment')
            return result
        except Exception as e:
            _logger.error('Chat failed for quest %s: %s', self.name, e)
            if session:
                session.write({'status': 'error', 'finish_reason': str(e)[:200]})
            return None

    # ── Cron with filter_domain ──

    def cron(self, records=None):
        """Run quest as cron job, optionally filtered by domain."""
        self.ensure_one()
        if self._check_quest_error():
            self.write({'last_status': 'error', 'last_run': fields.Datetime.now()})
            return None

        cron_init = self.init_type_ids.filtered(
            lambda it: it.init_type == 'cron' and it.enabled)
        if cron_init and cron_init[0].filter_domain:
            try:
                from odoo.tools.safe_eval import safe_eval
                domain = safe_eval(cron_init[0].filter_domain)
                model_name = self.model_ids[0].model if self.model_ids else 'res.partner'
                records = self.env[model_name].search(domain)
            except Exception as e:
                _logger.warning('Invalid filter_domain: %s', e)

        return self.action_run_scheduled()

    # ── Buzz workspace methods ──

    def _buzz_route_message(self, message):
        """Select the best agent in this buzz workspace for a message.

        Priority:
        1. @mention of agent alias
        2. Trigger word match
        3. LLM routing (if enabled and no clear match)
        4. Fallback to first agent / leader
        """
        self.ensure_one()
        if self.orchestration_mode != 'buzz':
            return None

        body = (message.body or '').lower()
        agent_rels = self.agent_ids
        if not agent_rels:
            return None

        # 1. @mention
        for rel in agent_rels:
            alias = (rel.agent_id.alias_name or '').lower()
            if alias and f'@{alias}' in body:
                return rel

        # 2. Trigger words
        for rel in agent_rels:
            triggers = [t.strip().lower() for t in (rel.agent_id.trigger_words or '').split(',') if t.strip()]
            if any(t in body for t in triggers):
                return rel

        # 3. LLM routing (if enabled)
        if self.buzz_use_llm_router and len(agent_rels) > 1:
            try:
                return self._buzz_llm_route(body, agent_rels)
            except Exception:
                pass

        # 4. Leader / fallback
        leader = agent_rels.filtered(lambda r: r.role == 'leader')
        if leader:
            return leader[0]
        return agent_rels[0]

    def _buzz_llm_route(self, body, agent_rels):
        """Use LLM to select the best agent for a message.

        Change ai-orchestration-tidy-up 7.6: använder coworkerns modell
        (ProviderFactory + shared LLMRouter) — inte hårdkodad gpt-4o.
        """
        import asyncio
        from odoo.addons.ai_agent_core.core.router import LLMRouter
        from odoo.addons.ai_agent_core.core.provider import ProviderFactory

        provider, model = ProviderFactory.from_coworker(self)
        if not provider:
            return None

        agents = [
            {'name': r.agent_id.name,
             'description': r.agent_id.description or r.agent_id.ai_role or '',
             'triggers': []}
            for r in agent_rels
        ]
        router = LLMRouter(provider, model or '')
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                decision = loop.run_until_complete(
                    router.route(body, agents, max_tokens=100))
            finally:
                loop.close()
        except Exception as e:
            _logger.warning('Buzz LLM route failed: %s', e)
            return None

        name = ((decision or {}).get('agent')
                or ((decision or {}).get('agents') or [None])[0])
        for rel in agent_rels:
            if rel.agent_id.name == name:
                return rel
        return None

    def _buzz_run_agent(self, agent_rel, prompt, history_text=''):
        """Run a single agent using its own model/skills/tools (7.5).

        Change ai-orchestration-tidy-up:
          - 7.4: injicerar session summary som kontext
          - 7.5: agentens EGNA tools/skills (via force_agent i run())
          - fix: force_model var inte stött av run() → TypeError
        """
        self.ensure_one()
        agent = agent_rel.agent_id
        system = self.description or ''
        if agent.identity_id:
            system = agent.identity_id.system_prompt or system
        elif agent.ai_backstory:
            system += f"\n\n{agent.ai_backstory}"
        if agent.ai_role:
            system = f"Role: {agent.ai_role}\nGoal: {agent.ai_goal or ''}\n" + system

        # Session summary som kontext (7.4)
        summary_ctx = ''
        if self.buzz_channel_session_id \
                and self.buzz_channel_session_id.summary:
            summary_ctx = (
                "\n\n## Sammanfattning av tidigare konversation\n"
                f"{self.buzz_channel_session_id.summary}")

        # Use agent's own model if available
        model = agent.model_id.name if agent and agent.model_id else None
        return self.run(
            prompt, system_prompt=system + summary_ctx,
            force_model=model, force_agent=agent)

    def _buzz_post_as_agent(self, agent, body, channel, internal=False):
        """Post a message to the channel as the agent's partner."""
        self.ensure_one()
        if not agent.partner_id or not channel:
            return None
        return channel.sudo().with_context(ai_buzz_internal=internal).message_post(
            body=f'<p>{body}</p>',
            message_type='comment',
            subtype_xmlid='mail.mt_comment',
            author_id=agent.partner_id.id,
        )

    def _buzz_auto_agent_count(self):
        """Count auto-created agents in this quest."""
        self.ensure_one()
        return len(self.agent_ids.filtered('is_auto_created'))

    def _buzz_generate_persona(self, topic):
        """Use LLM to generate a JSON persona for a new agent."""
        self.ensure_one()
        prompt_template = self.agent_creator_prompt or DEFAULT_AGENT_CREATOR_PROMPT
        prompt = prompt_template.format(topic=topic)
        try:
            raw = self.with_context(ai_single_agent_run=True).run(
                prompt, system_prompt=prompt_template.split('{topic}')[0])
            # Extract JSON from markdown code block if present
            text = raw or ''
            if '```json' in text:
                text = text.split('```json')[1].split('```')[0]
            elif '```' in text:
                text = text.split('```')[1].split('```')[0]
            return json.loads(text.strip())
        except Exception as e:
            _logger.warning('Buzz persona generation failed: %s', e)
            return {
                'name': f'Specialist: {topic[:30]}',
                'alias_name': topic.lower().replace(' ', '-')[:20],
                'personality': f'Hjälpsam specialist inom {topic}.',
                'style': 'Korta, tydliga svar.',
                'values': 'Korrekthet > snabbhet.',
                'boundaries': 'Gör inga juridiska tolkningar.',
                'trigger_words': topic,
                'avatar_description': f'Avatar for {topic} specialist',
            }

    def _buzz_suggest_or_create_agent(self, topic):
        """Create or suggest a new agent for an uncovered topic.

        If allow_auto_create_agents is True and under limit, create directly.
        Otherwise suggest to the user.
        """
        self.ensure_one()
        if self._buzz_auto_agent_count() >= self.max_auto_agents:
            return {
                'created': False,
                'reason': 'limit',
                'message': _('Max %s auto-created agents reached.') % self.max_auto_agents,
            }

        persona = self._buzz_generate_persona(topic)
        identity = self.env['ai.identity'].sudo().create({
            'name': persona['name'],
            'personality': persona.get('personality', ''),
            'style': persona.get('style', ''),
            'values': persona.get('values', ''),
            'boundaries': persona.get('boundaries', ''),
        })
        agent = self.env['ai.agent'].sudo().create({
            'name': persona['name'],
            'alias_name': persona.get('alias_name', topic.lower().replace(' ', '-')[:20]),
            'trigger_words': persona.get('trigger_words', topic),
            'description': persona.get('description', f'Auto-created specialist for {topic}'),
            'identity_id': identity.id,
        })
        self.env['ai.coworker.agent'].sudo().create({
            'coworker_id': self.id,
            'agent_id': agent.id,
            'role': 'member',
            'is_auto_created': True,
        })

        # Generate AI avatar (falls back to default initials if no image model)
        agent._generate_avatar_image(persona.get('avatar_description', ''))

        if self.allow_auto_create_agents:
            return {
                'created': True,
                'agent': agent,
                'message': _('I have called in %s to help with this.') % agent.name,
            }
        return {
            'created': False,
            'suggested': agent,
            'reason': 'manual',
            'message': _('Should I call in %s to help with this?') % agent.name,
        }

    def _buzz_ensure_channel_session(self):
        """Get or create the shared web UI session for this buzz workspace."""
        self.ensure_one()
        if self.buzz_channel_session_id:
            return self.buzz_channel_session_id
        session = self.env['ai.coworker.session'].sudo().create({
            'coworker_id': self.id,
            'name': f'Buzz: {self.name}',
            'thread_name': self.name,
            'status': 'active',
            'user_id': self.env.ref('base.user_root').id,
        })
        self.buzz_channel_session_id = session.id
        return session

    def _buzz_sync_message_to_session(self, body, role='user', agent=None):
        """Mirror a channel message to the shared web UI session."""
        self.ensure_one()
        if self.orchestration_mode != 'buzz':
            return None
        session = self._buzz_ensure_channel_session()
        seq = len(session.session_line_ids) + 1
        prefix = ''
        if agent and role == 'assistant':
            prefix = f'[{agent.name}] '
        line = self.env['ai.coworker.session.line'].sudo().create({
            'session_id': session.id,
            'sequence': seq,
            'role': role,
            'content': f'{prefix}{body}'[:4000],
        })
        # Generera sammanfattning vid tröskeln (change 7.4)
        self._buzz_maybe_summarize_session(session)
        return line

    def _buzz_maybe_summarize_session(self, session=None):
        """Generera session summary när tröskeln passeras (7.4).

        Tröskeln (antal meddelanden) är konfigurerbar via
        ir.config_parameter ai_agent_core.buzz_summary_threshold (default 50).
        Sammanfattningen injiceras som kontext till nya agenter via
        _buzz_run_agent istället för hela råhistoriken.
        """
        self.ensure_one()
        if self.orchestration_mode != 'buzz':
            return False
        session = session or self._buzz_ensure_channel_session()
        threshold = int(self.env['ir.config_parameter'].sudo().get_param(
            'ai_agent_core.buzz_summary_threshold', '50') or 50)
        total = len(session.session_line_ids)
        if total < threshold:
            return False
        # Sammanfatta igen först när minst hälften av tröskeln nya
        # meddelanden tillkommit sedan förra sammanfattningen.
        if session.summary_message_count and \
                total - session.summary_message_count < max(threshold // 2, 1):
            return False

        lines = session.session_line_ids.sorted('sequence')
        transcript = '\n'.join(
            f"[{ln.role}] {ln.content[:500]}" for ln in lines[-threshold * 2:])
        prompt = (
            f"Sammanfatta följande konversation i ett Buzz-team. "
            f"Fånga: ämnen, beslut, öppna frågor och agenternas roller. "
            f"Skriv på svenska, max 300 ord.\n\n{transcript}")
        try:
            import asyncio
            from odoo.addons.ai_agent_core.core.provider import ProviderFactory
            from odoo.addons.ai_agent_core.core.loop import AgentLoop, AgentConfig
            provider, model = ProviderFactory.from_coworker(self)
            if not provider:
                return False
            loop = AgentLoop(
                provider=provider,
                config=AgentConfig(
                    model=model or '',
                    system_prompt='Du sammanfattar konversationer.',
                    max_rounds=1))

            async def _run():
                resp = await loop.run(prompt)
                return resp.text if hasattr(resp, 'text') else str(resp)

            evloop = asyncio.new_event_loop()
            asyncio.set_event_loop(evloop)
            try:
                summary = evloop.run_until_complete(_run())
            finally:
                evloop.close()
            session.sudo().write({
                'summary': summary[:4000],
                'summary_message_count': total,
            })
            _logger.info('Buzz session %s sammanfattad (%d meddelanden)',
                         session.id, total)
            return True
        except Exception as e:
            _logger.warning('Buzz session summary failed: %s', e)
            return False

    def _buzz_chat(self, message, channel, msg_text, history_ctx='', depth=0):
        """Handle a channel message in buzz workspace mode."""
        self.ensure_one()

        # Avoid responding to our own agents (prevents loops)
        author_partner = message.author_id
        agent_partners = self.agent_ids.mapped('agent_id.partner_id')
        if author_partner and author_partner in agent_partners:
            return None

        # Skip messages mirrored from the web UI to avoid double-processing
        if self.env.context.get('buzz_web_ui_sync'):
            return None

        # Mirror incoming user/human message to shared web UI session
        is_human = author_partner not in agent_partners
        if is_human:
            self._buzz_sync_message_to_session(msg_text, role='user')

        # Limit consecutive agent turns (configurable)
        max_depth = self.buzz_a2a_max_depth or 3
        if depth >= max_depth:
            _logger.info('Buzz chat max depth reached for quest %s', self.name)
            return None

        # Route to the best agent
        agent_rel = self._buzz_route_message(message)

        if not agent_rel:
            # No agent matched — try proactive creation
            topic = msg_text[:50]
            result = self._buzz_suggest_or_create_agent(topic)
            if result.get('created') and result.get('agent'):
                agent_rel = self.agent_ids.filtered(
                    lambda r: r.agent_id == result['agent'])
                if agent_rel:
                    agent_rel = agent_rel[0]
                # Notify channel about the new agent
                self._buzz_post_as_agent(
                    result['agent'], result['message'], channel)
            else:
                # Post suggestion or limit message as quest (bot_user)
                if channel:
                    channel.message_post(
                        body=f'<p>{result["message"]}</p>',
                        message_type='comment', subtype_xmlid='mail.mt_comment')
                return None

        if not agent_rel:
            return None

        # Run the selected agent
        try:
            answer = self._buzz_run_agent(agent_rel, msg_text, history_ctx)
            if not answer or not channel:
                return answer

            posted = self._buzz_post_as_agent(
                agent_rel.agent_id, answer, channel, internal=(depth > 0))

            # Mirror agent response to shared web UI session
            self._buzz_sync_message_to_session(answer, role='assistant', agent=agent_rel.agent_id)

            # Agent-to-agent: if answer @mentions another agent, route to them
            next_rel = self._buzz_route_message_from_text(answer, exclude=agent_rel.agent_id)
            if next_rel and depth < max_depth - 1:
                # Build a synthetic message for the next agent
                synthetic = self.env['mail.message'].new({
                    'body': f'<p>{answer}</p>',
                    'model': 'discuss.channel',
                    'res_id': channel.id,
                    'author_id': agent_rel.agent_id.partner_id.id,
                })
                self._buzz_chat(
                    synthetic, channel, answer, history_ctx, depth=depth + 1)

            return answer
        except Exception as e:
            _logger.error('Buzz chat failed for quest %s: %s', self.name, e)
            return None

    def _buzz_route_message_from_text(self, text, exclude=None):
        """Route based on plain text, optionally excluding an agent."""
        self.ensure_one()
        text_lower = (text or '').lower()
        for rel in self.agent_ids:
            agent = rel.agent_id
            if exclude and agent == exclude:
                continue
            alias = (agent.alias_name or '').lower()
            if alias and f'@{alias}' in text_lower:
                return rel
        return None

    # ── Coworker channel integration ──

    partner_id = fields.Many2one(
        'res.partner', string='Channel Partner',
        help='Created automatically when coworker joins a discuss channel.')

    def _ensure_partner(self):
        """Create or return res.partner for this coworker.
        
        Idempotent — does nothing if partner_id already set.
        Created lazily — only when coworker joins a channel.
        """
        self.ensure_one()
        if self.partner_id:
            return self.partner_id
        partner = self.env['res.partner'].sudo().create({
            'name': self.name or 'AI Coworker',
            'email': f'{self.channel_alias or self.name or "coworker"}@coworker.vertel.se',
            'is_company': False,
        })
        self.write({'partner_id': partner.id})
        return partner

    def _route_message(self, message):
        """Route an incoming discuss message to this coworker for AI processing.

        Checks response_mode, then calls chat() to process through AgentLoop
        and post a response.
        Falls back to _buzz_route_message if in buzz mode.
        """
        self.ensure_one()
        if self._get_effective_orchestration_mode() == 'buzz':
            return self._buzz_route_message(message)

        from odoo.tools.mail import html2plaintext

        # Find the active chat/channel init_type for this coworker
        active_init = self.init_type_ids.filtered(
            lambda it: it.init_type in ('chat', 'channel') and it.enabled
        )
        if not active_init:
            _logger.warning(
                "No active chat/channel init_type for coworker %s",
                self.name,
            )
            return None

        init = active_init[0]

        # Check response_mode
        if init.response_mode == 'mention':
            # Already matched by @mention in mail_message.py — proceed
            pass
        elif init.response_mode == 'trigger':
            # Check trigger words
            if not init.chat_trigger_words:
                return None
            msg_text = html2plaintext(message.body or '')
            triggers = [w.strip().lower() for w in init.chat_trigger_words.split(',')]
            if not any(t in msg_text.lower() for t in triggers):
                return None
        # 'always' — no check needed

        # Get channel and bot user
        channel = self.env['discuss.channel'].browse(message.res_id)
        if not channel.exists():
            return None

        # Resolve bot user from chat init_type, or channel partners
        bot_user = None
        if init.init_type == 'chat' and init.chat_user_id:
            bot_user = init.chat_user_id

        # Process via chat() method (creates session, runs AgentLoop, posts response)
        return self.chat(message, channel=channel, bot_user=bot_user)

    @api.model
    def _migrate_init_types(self):
        """Create ai.coworker.init_type records for quests missing them.
        
        Idempotent — safe to call multiple times.
        Called automatically on module upgrade.
        """
        quests = self.search([('init_type_ids', '=', False)])
        created = 0
        for quest in quests:
            old_type = quest.init_type or 'manual'
            vals = {
                'coworker_id': quest.id,
                'init_type': old_type,
                'active': quest.status == 'active',
            }
            if old_type == 'web_ui':
                pass
            elif old_type == 'chat':
                vals['chat_user_id'] = quest.chat_user_id.id if quest.chat_user_id else False
                vals['use_chat_history'] = quest.use_chat_history
                vals['chat_history_limit'] = quest.chat_history_limit
            elif old_type == 'channel':
                vals['channel_id'] = quest.channel_id.id if quest.channel_id else False
            elif old_type == 'mail':
                pass
            elif old_type == 'cron':
                vals['cron_id'] = quest.cron_id.id if quest.cron_id else False
                vals['filter_domain'] = quest.filter_domain
            elif old_type == 'server_action':
                vals['server_action_id'] = quest.server_action_id.id if quest.server_action_id else False

            self.env['ai.coworker.init_type'].create(vals)
            created += 1

        if created:
            _logger.info('Migration: Created %d ai.coworker.init_type records', created)
        # Also seed any missing types for ALL quests
        self._seed_all_init_types()
        return created

    @api.model
    def _seed_all_init_types(self):
        """Seed ALL 9 init_type records for every quest that's missing any.
        
        This ensures all options (web_ui, chat, channel, mail, cron,
        server_action, powerbox, manual, openai_api) are available
        in the many2many_tags widget for every quest.
        """
        quests = self.search([])
        seeded = 0
        for quest in quests:
            existing = set(
                it.init_type for it in quest.init_type_ids
            )
            for itype, _label in INIT_TYPES:
                if itype not in existing:
                    self.env['ai.coworker.init_type'].create({
                        'coworker_id': quest.id,
                        'init_type': itype,
                        'enabled': itype in ('manual', 'web_ui'),
                    })
                    seeded += 1
        if seeded:
            _logger.info('Seeded %d missing init_type records', seeded)
        return seeded

    @api.model
    def _ensure_init_resources(self, records=None):
        """Säkerställ att alla aktiva init-types har sina resurser.

        - watch: base_automation + kopplad server action (kod som anropar
          rätt ai.coworker via _trigger_watch)
        - mail: mail.alias som pekar på ai.coworker.session

        Idempotent — anropas av <function> i data/ensure_init_resources.xml
        vid varje moduluppdatering (checkmodule --init kör inga migrationer).
        """
        watch_count = mail_count = 0
        # Fixa server action-koden på ALLA watch base_automations (även om
        # init-typen är avstängd — automationen kan fortfarande trigga).
        for it in self.env['ai.coworker.init_type'].search([
                ('init_type', '=', 'watch'),
                ('base_automation_id', '!=', False)]):
            try:
                if it.enabled:
                    it._ensure_watch()
                else:
                    it._ensure_watch_action()
                watch_count += 1
            except Exception as e:
                _logger.warning('ensure watch misslyckades för init %s: %s',
                                it.id, e)
        for it in self.env['ai.coworker.init_type'].search([
                ('enabled', '=', True), ('init_type', '=', 'mail')]):
            try:
                it._ensure_mail_alias()
                mail_count += 1
            except Exception as e:
                _logger.warning('ensure mail misslyckades för init %s: %s',
                                it.id, e)
        _logger.info('ensure_init_resources: watch=%s mail=%s',
                     watch_count, mail_count)
        return watch_count + mail_count

    def _ensure_all_init_types(self):
        """Skapa en komplett init_type-rad-uppsättning (en per INIT_TYPES-typ)
        för medarbetaren om rader saknas. Idempotent.

        Gör att many2many_checkboxes med domain [('coworker_id','=',id)] kan
        visa varje typ som en kryssruta — användaren väljer en av varje,
        rader skapas/aktiveras automatiskt av _inverse_active_init_types.
        """
        for rec in self:
            existing = {it.init_type for it in rec.init_type_ids}
            for itype, _label in INIT_TYPES:
                if itype not in existing:
                    self.env['ai.coworker.init_type'].create({
                        'coworker_id': rec.id,
                        'init_type': itype,
                        # Web-UI och manual aktiva som default; övriga
                        # skapas avstängda men synliga i kryssrute-UI:t.
                        'enabled': itype in ('manual', 'web_ui'),
                        'sequence': 10,
                    })

    # ── Record Context Injection (ported from ai_agent_context) ──

    def _build_injection_prompt(self, user=None, agent=None, prompt='',
                                record=None, max_chars=6000):
        """Gemensam injiceringsfunktion (agent-memory-governance 3.x).

        ENDA injiceringskällan (full refaktor): användare → rekordkontext →
        minne (company/personal/coworker) → mission. Respekterar AI
        Medarbetarens memory_scopes + kopplingens hårda block och nivåer.
        Sessionhistorik hanteras av anroparen (history-parametern).

        Args:
            user: Aktuell användare (D2 — subjekt för personligt minne)
            agent: Specifik agent (kopplingens block/level gäller)
            record: Aktuell Odoo-post (powerbox/rekordkontext)
            prompt: aktuell fråga (används som query för L1-sök)
        """
        user = user or self.env.user
        parts = []
        budget = max_chars

        # 2. Aktuell användare — med TYDLIG instruktion så modellen aldrig
        # frågar vem användaren är (antaganden-delen av Agent Identity).
        if user and user.id and user.login and user.login != 'public':
            partner = user.partner_id
            company = user.company_id
            user_parts = [
                f"- Namn: {partner.name if partner else user.login}",
                f"- Inloggning: {user.login}",
                f"- E-post: {partner.email if partner and partner.email else '-'}",
                f"- Företag: {company.name if company else '-'}",
            ]
            # Befattning från HR (personal-memory-sources)
            try:
                emp = self.env['hr.employee'].search([
                    ('work_email', '=', user.login),
                ] + ([('company_id', '=', company.id)] if company else []),
                    limit=1)
                if not emp and partner:
                    emp = self.env['hr.employee'].search(
                        [('work_contact_id', '=', partner.id)], limit=1)
                if emp and emp.job_id:
                    user_parts.append(f"- Befattning: {emp.job_id.name}")
            except Exception:
                pass
            user_name = partner.name if partner else user.login
            parts.append(
                "## Aktuell användare\n"
                f"Du pratar med {user_name} ({user.login}). "
                f"Använd DENNA identitet för alla frågor om användaren — "
                f"fråga ALDRIG användaren vem de är.\n"
                + '\n'.join(user_parts)
            )

        # 3. Rekordkontext (L1-L3 från tidigare _extra_context) — aldrig
        # avbryt hela injektionen; rekordkontext är best-effort.
        if self.context_injection_enabled:
            try:
                ch_ctx = self._get_channel_context()
                if ch_ctx:
                    parts.append(
                        f"## User Context\n"
                        f"The user is currently viewing: {ch_ctx['model']}"
                        + (f" (record ID: {ch_ctx['record_id']})" if ch_ctx.get('record_id') else "")
                        + (f" in {ch_ctx['view_type']} view.\n" if ch_ctx.get('view_type') else ".\n")
                    )
                if record is None:
                    record = self._get_ai_context_record() or self._get_session_context_record()
                if record and record.exists():
                    parts.append(
                        f"## Current Record: {record._name} (ID: {record.id})\n"
                        f"You are interacting within this Odoo record. "
                        f"Use the field data below to answer questions about it.\n"
                    )
                    json_data = record._ai_serialize_fields_data(
                        max_fields=self.context_max_fields)
                    parts.append(f"### Record Fields\n```json\n{json_data}\n```\n")
                    if self.context_include_chatter and hasattr(
                            record, '_ai_serialize_messages_data'):
                        chatter = record._ai_serialize_messages_data()
                        if chatter:
                            clines = chatter.split('\n')
                            if len(clines) > self.context_chatter_limit:
                                clines = clines[-self.context_chatter_limit:]
                                chatter = '\n'.join(clines) + \
                                    "\n(older messages omitted)"
                            parts.append(
                                f"### Chatter History (oldest -> newest)\n{chatter}\n")
            except Exception as e:
                _logger.error('Rekordkontext misslyckades: %s', e)

        # Kopplingens block + effektiva nivåer (agent = specifik, annars medarbetarnivå)
        link = None
        if agent:
            link = self.agent_ids.filtered(lambda l: l.agent_id.id == agent.id)[:1]
        scope_codes = set(self.memory_scopes.mapped('code'))

        # 4-6. Minne per scope
        if 'ai.okf.concept' in self.env and scope_codes:
            company_id = self.company_id.id or self.env.company.id
            for scope in ('company', 'personal', 'coworker'):
                if scope not in scope_codes:
                    continue
                if link and getattr(link, 'block_%s' % scope, False):
                    continue  # hårt block
                level = self.memory_level or 'L1'
                if link:
                    level = link._effective_level(scope)
                if scope == 'company':
                    owner_id = company_id
                elif scope == 'personal':
                    owner_id = user.id
                else:
                    owner_id = self.id
                try:
                    block = self.env['ai.okf.concept']._okf_build_system_prompt_block(
                        scope, owner_id, query=prompt or self.description,
                        max_chars=min(budget // 2, 2000),
                        injection_level={
                            'L0': 'summary_only',
                            'L1': 'summary_and_key',
                            'L2': 'summary_and_key',
                            'L3': 'full',
                        }.get(level, 'summary_and_key'))
                    if block:
                        parts.append(block)
                        budget -= len(block)
                except Exception as e:
                    _logger.debug('Injection block %s misslyckades: %s', scope, e)

        # 6. Mission/values
        if self.use_company_info:
            company = self.env.user.company_id
            cinfo = []
            if company.company_mission:
                cinfo.append(f"## Company Mission\n{company.company_mission}")
            if company.company_values:
                cinfo.append(f"## Company Values\n{company.company_values}")
            if cinfo:
                parts.append("\n\n".join(cinfo))

        return "\n\n".join(parts)

    def _extra_context(self):
        """Wrapper för bakåtkompatibilitet — ENDA injiceringskällan är
        _build_injection_prompt (full refaktor)."""
        res = super()._extra_context() if hasattr(super(), '_extra_context') else ''
        inj = self._build_injection_prompt(user=self.env.user, prompt='')
        return (res + '\n\n' + inj).strip() if inj else res

    def _detect_record(self, kwargs):
        """Detect context record from available sources."""
        # 1. Direct record parameter
        r = kwargs.get('record')
        if r and hasattr(r, 'exists') and r.exists():
            return r
        # 2. First from recordset
        records = kwargs.get('records')
        if records and len(records) > 0:
            return records[0]
        # 3. env.context (form button)
        ctx_m = self.env.context.get('context_record_model')
        ctx_id = self.env.context.get('context_record_id')
        if ctx_m and ctx_id:
            try:
                r = self.env[ctx_m].browse(int(ctx_id))
                if r.exists():
                    return r
            except Exception:
                pass
        # 4. Channel context
        ch = kwargs.get('channel')
        if ch:
            ch_model = getattr(ch, 'ai_context_model', False)
            ch_rid = getattr(ch, 'ai_context_record_id', False)
            if ch_model and ch_rid:
                try:
                    r = self.env[ch_model].browse(int(ch_rid))
                    if r.exists():
                        return r
                except Exception:
                    pass
        # 5. Message model/res_id
        msg = kwargs.get('message')
        if msg:
            for src in [msg, getattr(msg, 'parent_id', None)]:
                if not src:
                    continue
                m = getattr(src, 'model', None) or getattr(src, 'res_model', None)
                rid = getattr(src, 'res_id', None)
                if m and rid and m != 'discuss.channel':
                    try:
                        r = self.env[m].browse(rid)
                        if r.exists():
                            return r
                    except Exception:
                        pass
        # 6. Session objects
        if ch:
            sess = getattr(ch, 'ai_coworker_session_id', None)
            if sess and hasattr(sess, 'session_object_ids') and sess.session_object_ids:
                obj = sess.session_object_ids[0]
                if hasattr(obj, 'object_id') and obj.object_id:
                    return obj.object_id
        return None

    def _get_channel_context(self):
        """Get user view context from quest's linked discuss channel."""
        channel = self.channel_id
        if not channel:
            return None
        model = getattr(channel, 'ai_context_model', False)
        if not model:
            return None
        return {
            'model': model,
            'record_id': getattr(channel, 'ai_context_record_id', False),
            'view_type': getattr(channel, 'ai_context_view_type', False),
        }

    def _get_ai_context_record(self):
        """Get record from env.context."""
        m = self.env.context.get('_ai_context_model')
        rid = self.env.context.get('_ai_context_id')
        if m and rid:
            try:
                r = self.env[m].browse(int(rid))
                return r if r.exists() else None
            except Exception:
                pass
        return None

    def _get_session_context_record(self):
        """Get record from active sessions' objects."""
        active = self.session_ids.filtered(lambda x: x.status == 'active')
        for s in active:
            if hasattr(s, 'session_object_ids') and s.session_object_ids:
                obj = s.session_object_ids[0]
                if hasattr(obj, 'object_id') and obj.object_id:
                    return obj.object_id
        return None

    def _ensure_agent(self):
        """Auto-create a default agent (and assignment) when the coworker
        has none.

        Implements the coworker-agent-bridge rule (coworker = shell,
        agent = brain): a coworker in single mode has exactly 1 agent.
        Without an agent the chat/channel/cron runs silently fail
        ('You must assign at least one agent') — this prevents that.
        """
        for rec in self:
            if rec.agent_ids:
                continue
            default_model_id = rec.env['ir.config_parameter'].sudo().get_param(
                'ai_agent_core.default_model_id', '365')
            model = rec.env['ai.model'].browse(int(default_model_id))
            if not model.exists():
                model = rec.env['ai.model'].search(
                    [('provider_type', '=', 'bifrost')],
                    order='id asc', limit=1)
            agent = rec.env['ai.agent'].sudo().create({
                'name': rec.name or 'Default Agent',
                'description': f'Default agent for {rec.name or "coworker"}.',
                'model_id': model.id if model else False,
                'status': 'active',
                'sequence': 10,
            })
            rec.env['ai.coworker.agent'].sudo().create({
                'coworker_id': rec.id,
                'agent_id': agent.id,
                'sequence': 10,
                'role': 'member',
            })
            _logger.info('Auto-created default agent %s for coworker %s',
                         agent.name, rec.name)
        return True

    def _visible_models(self, init_type=''):
        """Return tillåtna modeller för en init_type, eller None = alla.

        - chat/channel/web_ui → None (alla modeller)
        - server_action/powerbox → model_ids-bundna modeller + aktuell kontext
        - cron/mail/webhook → coworkerns tool-bindningar (modeller som
          verktygen pekar på; utan bindning → None)
        """
        self.ensure_one()
        if init_type in ('chat', 'channel', 'web_ui'):
            return None
        if init_type in ('server_action', 'powerbox'):
            models = set(self.model_ids.mapped('model'))
            # aktuell rekordkontext läggs till i run/powerbox via env.context
            return models or None
        if init_type in ('cron', 'mail', 'webhook', 'openai_api'):
            models = set()
            for tool in self.tool_ids.filtered('active'):
                if tool.model_ids:
                    models |= set(tool.model_ids.mapped('model'))
            return models or None
        return None  # manual → alla

    def _ensure_default_coworker(self):
        """Adoptera/skapa default-coworkern "Allmän assistent" (idempotent).

        Anropas som <function> i data/default_coworker.xml vid varje
        datainläsning (install OCH checkmodule --init). Migrations körs inte
        av checkmodule (--init utan --update), så adoption av legacy-poster
        ("Allmän" från hooks/migration 1.8) måste ske här.

        Steg:
        1. Behåll äldsta is_default-coworkern, avaktivera ev. duplikat
        2. Byt namn "Allmän" → "Allmän assistent"
        3. Bind xmlids (coworker, lead-agent, länk) via ir.model.data
        4. Döp om lead-agenten "Allmän assistent" → "Allmän kärna"
        5. Säkerställ web_ui-init aktiverad (chatten kräver enabled)
        """
        defaults = self.search([('is_default', '=', True)], order='id asc')
        if not defaults:
            return True  # data-XML skapar på ny installation

        keep = defaults[0]
        # 1. Avlägsna duplikat (t.ex. data-XML-kopia skapad före adoption)
        for dup in defaults[1:]:
            dup_agent_ids = dup.agent_ids.ids
            dup.write({'active': False, 'is_default': False})
            for agent in self.env['ai.agent'].sudo().browse(dup_agent_ids):
                if not self.env['ai.coworker.agent'].search_count([
                    ('agent_id', '=', agent.id),
                    ('coworker_id.active', '=', True),
                ]):
                    agent.write({'active': False})
            _logger.info('Adopterade bort duplikat-coworker %s (%s)',
                         dup.id, dup.name)

        # 2. Byt namn
        if keep.name == 'Allmän':
            keep.write({'name': 'Allmän assistent'})
            _logger.info('Döpte om default-coworker → Allmän assistent')

        # 3. Bind xmlids
        IrModelData = self.env['ir.model.data'].sudo()

        def _bind(name, model, res_id):
            existing = IrModelData.search([
                ('module', '=', 'ai_agent_core'), ('name', '=', name),
            ], limit=1)
            if existing:
                if existing.res_id != res_id or existing.model != model:
                    existing.write({'res_id': res_id, 'model': model})
            else:
                IrModelData.create({
                    'module': 'ai_agent_core', 'name': name,
                    'model': model, 'res_id': res_id, 'noupdate': True,
                })

        _bind('coworker_default_assistent', 'ai.coworker', keep.id)

        # 4. Lead-agent + länk
        link = self.env['ai.coworker.agent'].sudo().search([
            ('coworker_id', '=', keep.id),
        ], order='sequence, id asc', limit=1)
        if link:
            agent = link.agent_id
            _bind('coworker_agent_default_core', 'ai.coworker.agent', link.id)
            _bind('agent_default_core', 'ai.agent', agent.id)
            if agent.name == 'Allmän assistent':
                agent.write({'name': 'Allmän kärna'})
                _logger.info('Döpte om lead-agent → Allmän kärna')

        # 5. Säkerställ web_ui-init aktiverad
        if not keep.init_type_ids.filtered(
                lambda it: it.init_type == 'web_ui' and it.enabled):
            self.env['ai.coworker.init_type'].sudo().create({
                'coworker_id': keep.id,
                'init_type': 'web_ui',
                'enabled': True,
                'show_in_chat': True,
            })
            _logger.info('Skapade web_ui-init för default-coworkern')

        # 5b. Seeda minnesinställningar om tomma (default-coworkern skapades
        # före memory-governance-featuren).
        if not keep.memory_scopes:
            keep._seed_memory_settings()
            if not keep.memory_scopes:
                scope_model = self.env['ai.memory.scope']
                codes = {s.code: s for s in scope_model.search([])}
                keep.memory_scopes = [(6, 0, [
                    codes[c].id for c in ('company', 'personal', 'coworker')
                    if c in codes])]

        # 6. Supervisor-läge med 3 agenter (odoo-model-tools change):
        #    kärna (lead) + Odoo-specialist + Research. Uppgraderar även
        #    legacy-defaults som hamnat i single/linear (test_seed_is_idempotent).
        if not keep.orchestration_mode or keep.orchestration_mode in (
                'single', 'linear'):
            keep.write({'orchestration_mode': 'supervisor'})
            _logger.info('Satte default-coworker → supervisor-läge')

        Agent = self.env['ai.agent'].sudo()
        CoworkerAgent = self.env['ai.coworker.agent'].sudo()

        def _ensure_agent(xmlid, name, ai_role, description, skills=()):
            """Hämta agent via xmlid; skapa om den saknas. Idempotent."""
            existing = IrModelData.search([
                ('module', '=', 'ai_agent_core'), ('name', '=', xmlid),
            ], limit=1)
            if existing and existing.res_id:
                agent = Agent.browse(existing.res_id)
                if agent.exists():
                    return agent
            agent = Agent.create({
                'name': name,
                'ai_role': ai_role,
                'description': description,
                'status': 'active',
            })
            _bind(xmlid, 'ai.agent', agent.id)
            if skills:
                found = self.env['ai.skill'].sudo().search(
                    [('name', 'in', list(skills))])
                if found:
                    agent.write({'skill_ids': [(6, 0, found.ids)]})
            _logger.info('Skapade agent %s (%s)', name, xmlid)
            return agent

        def _ensure_link(coworker, agent, role='member', sequence=20):
            if not CoworkerAgent.search([
                ('coworker_id', '=', coworker.id),
                ('agent_id', '=', agent.id),
            ], limit=1):
                CoworkerAgent.create({
                    'coworker_id': coworker.id,
                    'agent_id': agent.id,
                    'role': role,
                    'sequence': sequence,
                })
                _logger.info('Länkade agent %s → coworker %s',
                             agent.name, coworker.name)

        # Odoo-specialist — affärsverktyg + odoo-core-skill
        odoo_agent = _ensure_agent(
            'agent_odoo_business', 'Odoo-specialist',
            'Odoo Specialist',
            'Affärsexpert på Odoo-modeller: söker, skapar och kör affärsflöden '
            'via generiska modellverktyg och följer odoo-core-skillen.',
            skills=('odoo-core',),
        )
        _ensure_link(keep, odoo_agent, role='member', sequence=20)

        # Research — webb + youtube
        research_agent = _ensure_agent(
            'agent_research', 'Research',
            'Web Research',
            'Research-agent: söker information på webben, hämtar sidor och '
            'bearbetar YouTube-innehåll via youtube-skills.',
            skills=('youtube-transcript', 'youtube-search',
                    'youtube-channels', 'youtube-playlist', 'youtube-full'),
        )
        _ensure_link(keep, research_agent, role='member', sequence=30)

        return True

    def _learn_from_session(self, session):
        """Hermes-lärande (agent-memory-governance 4.x).

        Vid learning=active: LLM-reflektion över sessionen → 1-3 koncept
        → scope-routing + block → trust-gate → _okf_upsert (ADD-only,
        attribution, lineage).
        """
        import json as _json
        self.ensure_one()
        if self.learning != 'active':
            return 0
        if not session or not session.session_line_ids:
            return 0
        if 'ai.okf.concept' not in self.env:
            return 0

        # Samla konversationen
        lines = session.session_line_ids.sorted('sequence')
        conversation = '\n'.join(
            f"[{l.role}] {l.content[:500]}" for l in lines[-40:])
        if not conversation.strip():
            return 0

        # 1. LLM-reflektion
        concepts = self._extract_concepts_from_conversation(conversation)
        if not concepts:
            return 0

        written = 0
        for concept in concepts:
            scope = concept.get('scope', 'personal')
            summary = (concept.get('summary') or '').strip()[:1000]
            if not summary or scope not in ('company', 'personal', 'coworker'):
                continue
            # 2. Scope-routing + block (lead-regeln = medarbetarens scopes)
            scope_codes = set(self.memory_scopes.mapped('code'))
            if scope not in scope_codes:
                _logger.info('Lärande: scope %s ej aktivt — kasserat (%s)',
                             scope, summary[:60])
                self._record_learning_discard(session, scope, summary)
                continue
            # 3. Trust-gate
            direct = self.hitl_threshold == 'autonomous' or (
                self.hitl_threshold == 'high_risk' and scope == 'coworker')
            if not direct:
                self._record_learning_proposal(session, scope, summary)
                continue
            # 4. Skrivning via _okf_upsert
            try:
                if scope == 'company':
                    owner = {'owner_company_id': self.company_id.id or self.env.company.id}
                elif scope == 'personal':
                    owner = {'owner_user_id': (session.user_id.id or self.env.user.id)}
                else:
                    owner = {'owner_coworker_id': self.id}
                self.env['ai.okf.concept']._okf_upsert(
                    'learning',
                    concept_key=f'learned.{scope}.{session.id}.{len(summary[:40])}',
                    summary=summary,
                    title=summary[:80],
                    source_ref=f'ai.coworker.session,{session.id}',
                    attribution=[{
                        'source': f'ai.coworker.session.line,{lines[-1].id}',
                        'role': 'conversation',
                    }],
                    generated_by='learning',
                    **owner,
                )
                written += 1
            except Exception as e:
                _logger.warning('Lärande-skrivning misslyckades: %s', e)
        if written:
            _logger.info('Lärde mig %d koncept från session %s',
                         written, session.id)
        return written

    def _extract_concepts_from_conversation(self, conversation):
        """LLM-reflektion: föreslå 1-3 bestående koncept."""
        import json as _json
        import asyncio
        try:
            from odoo.addons.ai_agent_core.core.provider import (
                ProviderFactory, BifrostProvider)
            from odoo.addons.ai_agent_core.core.loop import AgentLoop, AgentConfig

            provider, _model = ProviderFactory.from_coworker(self)
            provider = provider or BifrostProvider(
                base_url='http://192.168.11.150:8080/v1',
                virtual_key='opencode')
            loop = AgentLoop(provider=provider, tools=[], config=AgentConfig(
                model='cerebras/gpt-oss-120b', max_rounds=1, max_tokens=1500))
            prompt = (
                "Granska konversationen och extrahera 1-3 BESTÅENDE fakta "
                "värda att minnas (inte småprat). Svara med JSON-lista: "
                "[{\"summary\": \"kort fakta\", \"scope\": "
                "\"personal|company|coworker\"}].\n\n"
                f"Konversation:\n{conversation}"
            )
            result = asyncio.run(loop.run(prompt))
            text = (result.text or '').strip()
            # Ta bort ev. ```json-omslag
            if '```' in text:
                text = text.split('```')[1] if '```json' in text else text
                text = text.replace('json', '', 1).strip()
            data = _json.loads(text)
            if isinstance(data, dict):
                data = data.get('concepts', data.get('facts', []))
            return data if isinstance(data, list) else []
        except Exception as e:
            _logger.warning('Reflektion misslyckades: %s', e)
            return []

    def _record_learning_discard(self, session, scope, summary):
        """Blockerat/lärande-område → notera i approval-kön."""
        try:
            if 'workspace.activity.suggestion' not in self.env:
                return
            self.env['workspace.activity.suggestion']._create_suggestion(
                summary=f"Lärande kasserat (blockerat {scope}): {summary[:60]}",
                detail=f"Medarbetaren ville lära sig {summary} men scope {scope} "
                       f"är inte aktivt/blockerat.",
                source='coworker', coworker_id=self.id, session_id=session.id,
            )
        except Exception:
            pass

    def _record_learning_proposal(self, session, scope, summary):
        """Trust-gate 'föreslå' → approval-kön."""
        try:
            if 'workspace.activity.suggestion' not in self.env:
                return
            self.env['workspace.activity.suggestion']._create_suggestion(
                summary=f"Föreslå inlärning ({scope}): {summary[:60]}",
                detail=summary,
                suggestion_type='mail.activity', source='coworker',
                coworker_id=self.id, session_id=session.id,
            )
        except Exception:
            pass

    def _check_quest_error(self):
        """Check quest configuration before running.

        Auto-creates a default agent when the coworker has none so chat /
        channel / cron runs do not silently return None.
        """
        if not self.agent_ids:
            self._ensure_agent()
        if not self.agent_ids:
            return _('You must assign at least one agent to the quest')
        inactive = self.agent_ids.filtered(lambda a: a.agent_id and a.agent_id.status != 'active')
        if inactive:
            return _('Check status on agents: %s') % ', '.join(inactive.mapped('agent_id.name'))
        return False

    def _build_loop(self, provider, tools, model, system_prompt, max_rounds=10):
        """Build AgentLoop or loop based on orchestration mode.

        Supports: single, supervisor, buzz, linear, conference, automation.
        Returns a callable loop object with a `run(prompt)` async method.
        """
        from odoo.addons.ai_agent_core.core.loop import AgentLoop, AgentConfig
        from odoo.addons.ai_agent_core.core.linear import LinearLoop

        # Supervisor model: explicit fält vinner, annars första agentens modell.
        supervisor_model = (
            self.supervisor_model_id.name
            if self.supervisor_model_id else model)

        # Inject record context into system prompt
        extra = self._extra_context() if self.context_injection_enabled else ''
        if extra:
            system_prompt = (system_prompt or '') + extra

        mode = self._get_effective_orchestration_mode()
        if self.env.context.get('ai_single_agent_run'):
            mode = 'single'

        # ── Automation: single + AUTO permission ──
        if mode == 'automation':
            from odoo.addons.ai_agent_core.core.permission import PermissionMode
            return AgentLoop(
                provider=provider, tools=tools,
                config=AgentConfig(
                    model=model, system_prompt=system_prompt,
                    max_rounds=max_rounds,
                    permission_mode='auto',
                ),
            )

        # ── Linear: sequential pipeline ──
        if mode == 'linear':
            agents = self.agent_ids.sorted('sequence')
            if not agents:
                return AgentLoop(provider=provider, tools=tools,
                    config=AgentConfig(model=model, system_prompt=system_prompt,
                        max_rounds=max_rounds))
            # Return a LinearLoop wrapper
            return LinearLoop(
                agents=agents, provider=provider, tools=tools,
                base_model=model, base_system=system_prompt,
                max_rounds=max_rounds)

        # ── Single agent mode ──
        if mode == 'single' or len(self.agent_ids) <= 1:
            agent_rel = self.agent_ids[:1] if self.agent_ids else None
            agent = agent_rel.agent_id if agent_rel else None
            agent_model = agent.model_id.name if agent and agent.model_id else model
            return AgentLoop(
                provider=provider, tools=tools,
                config=AgentConfig(
                    model=agent_model, system_prompt=system_prompt,
                    max_rounds=max_rounds,
                ),
            )

        # ── Conference: all agents answer, best answer wins ──
        if mode == 'conference':
            from odoo.addons.ai_agent_core.core.conference import ConferenceLoop
            from odoo.addons.ai_agent_core.core.supervisor import SupervisorConfig

            mechanism = (self.env.context.get('conference_mechanism')
                         or self.conference_mechanism or 'confidence')

            # Be agenterna svara med {answer, confidence} när mekanismen
            # kräver det (confidence/majority) — riktig confidence (7.1)
            conf_suffix = ''
            if mechanism in ('confidence', 'majority'):
                conf_suffix = (
                    '\n\nDu är i konferensläge. Svara med JSON: '
                    '{"answer": "...", "confidence": 0.0-1.0}. '
                    'Håll answer-delen komplett och användbar.')
            specialists = self._build_specialists(
                provider, tools, model,
                system_prompt + conf_suffix, max_rounds)
            return ConferenceLoop(
                router_provider=provider, agents=specialists,
                config=SupervisorConfig(router_model=supervisor_model),
                mechanism=mechanism,
            )

        # ── Multi-agent modes (supervisor/buzz) ──
        specialists = self._build_specialists(
            provider, tools, model, system_prompt, max_rounds)

        # Skill-baserad supervisor är DEFAULT (change ai-orchestration-tidy-up
        # 6.1): koppla orchestration.supervisor automatiskt om den saknas.
        skill_recipe = self._ensure_orchestration_skill() if mode == 'supervisor' else ''
        if mode != 'supervisor':
            # Buzz m.fl. — behåll eventuell explicit kopplad orchestration-skill
            for skill in self.skill_ids:
                if skill.name and 'orchestration' in skill.name.lower():
                    skill_recipe = skill.recipe_text or skill.improvement_guidance or ''
                    break

        # Skill-based supervisor: standard AgentLoop with specialist tools
        if skill_recipe:
            from odoo.addons.ai_agent_core.core.tools import specialist_tools
            # Build specialist tools from the specialist loops
            spec_tools = specialist_tools([
                (s.name, s.description, s.loop) for s in specialists
            ])
            for t in spec_tools:
                if t.name not in tools:
                    tools.register(t)
            # Build the supervisor agent prompt from skill recipe + specialist list
            agent_descriptions = '\n'.join(
                f"- **{s.name}**: {s.description}" for s in specialists)
            supervisor_prompt = (
                f"{skill_recipe}\n\n"
                f"Available specialists (call via tools):\n{agent_descriptions}\n"
                f"Delegera uppgifter genom att anropa rätt call_specialist_* verktyg.\n"
                f"När alla delar är klara, sammanställ ett slutgiltigt svar."
            )
            return AgentLoop(
                provider=provider, tools=tools,
                config=AgentConfig(
                    model=supervisor_model, system_prompt=supervisor_prompt,
                    max_rounds=max_rounds,
                ),
            )

        return SupervisorLoop(
            router_provider=provider,
            agents=specialists,
            config=SupervisorConfig(
                router_model=supervisor_model,
                skill_recipe=skill_recipe,
                max_rounds=3,
                max_iterations=self.max_iterations or 3,
                min_confidence=self.min_confidence or 0.8,
            ),
        )

    def _build_specialists(self, provider, tools, model, system_prompt, max_rounds=10):
        """Build list of SpecialistAgent from agent_ids."""
        from odoo.addons.ai_agent_core.core.loop import AgentLoop, AgentConfig
        from odoo.addons.ai_agent_core.core.supervisor import SpecialistAgent

        quest_skill_context = ''
        if self.skill_ids:
            quest_skill_context = '\n\n## Quest Orchestration Skills\n'
            for skill in self.skill_ids:
                quest_skill_context += f'\n### {skill.name}\n{skill.recipe_text or skill.description or ""}\n'

        specialists = []
        for agent_rel in self.agent_ids:
            agent = agent_rel.agent_id
            agent_model = agent.model_id.name if agent and agent.model_id else model
            agent_skills_context = ''
            for skill in agent.skill_ids:
                agent_skills_context += f'\n### Skill: {skill.name}\n{skill.recipe_text or skill.description or ""}\n'
            agent_system = (
                f"Role: {agent.ai_role or agent.name}\n"
                f"Goal: {agent.ai_goal or ''}\n"
                f"{quest_skill_context}"
                f"\n## Agent-Specific Skills\n{agent_skills_context}"
                + system_prompt
            )
            # Per-agent-injektion (agent-memory-governance 3.6): specialisten
            # får bara de scopen kopplingens block/level tillåter.
            try:
                agent_inj = self._build_injection_prompt(
                    user=self.env.user, agent=agent, prompt='')
                if agent_inj:
                    agent_system = (agent_system + '\n\n' + agent_inj).strip()
            except Exception:
                pass
            specialists.append(SpecialistAgent(
                name=agent.name,
                description=agent.get_agent_name(),
                loop=AgentLoop(
                    provider=provider, tools=tools,
                    config=AgentConfig(
                        model=agent_model,
                        system_prompt=agent_system,
                        max_rounds=max_rounds,
                    ),
                ),
                triggers=[],
            ))
        return specialists

    def _get_quest_memories(self, query: str, k: int = 3) -> str:
        """Search all FAISS memories for this quest and return context.

        Called by system prompt building to inject relevant memory
        into the agent's context.
        """
        self.ensure_one()
        memories = self.env['ai.memory'].search([
            ('quest_id', '=', self.id),
            ('archived', '=', False),
            ('memory_type', '=', 'faiss'),
        ])
        if not memories:
            return ''

        results = []
        for mem in memories:
            chunks = mem.faiss_search(query, k=k)
            if chunks:
                results.extend(chunks)

        if results:
            return '## Relevant memories\n' + '\n---\n'.join(results[:5])
        return ''

    def action_get_agents(self):
        return {
            'name': 'Agents', 'type': 'ir.actions.act_window',
            'res_model': 'ai.agent', 'view_mode': 'kanban,list,form',
            'target': 'current',
            'domain': [('id', 'in', self.agent_ids.mapped('agent_id').ids)],
        }

    def action_open_builder(self):
        """Open Quest Builder chat for this quest."""
        self.ensure_one()
        builder = self.env['ai.coworker'].search(
            [('name', '=', 'Quest Builder')], limit=1)
        if not builder:
            return {'type': 'ir.actions.act_url', 'url': '/ai/chat', 'target': 'new'}
        url = f'/ai/chat?coworker_id={builder.id}'
        if self.id:
            url += f'&context_quest={self.id}'
        return {'type': 'ir.actions.act_url', 'url': url, 'target': 'new'}

    def action_get_sessions(self):
        return {
            'name': 'Sessions', 'type': 'ir.actions.act_window',
            'res_model': 'ai.coworker.session', 'view_mode': 'list,form',
            'target': 'current',
            'domain': [('coworker_id', '=', self.id)],
        }

    def action_get_session_lines(self):
        return {
            'name': 'Session Lines', 'type': 'ir.actions.act_window',
            'res_model': 'ai.coworker.session.line',
            'view_mode': 'list,form,pivot',
            'target': 'current',
            'domain': [('session_id.coworker_id', '=', self.id)],
        }

    def action_get_session_objects(self):
        if 'ai.session.object' not in self.env:
            return {'type': 'ir.actions.act_window_close'}
        return {
            'name': 'Objects', 'type': 'ir.actions.act_window',
            'res_model': 'ai.session.object',
            'view_mode': 'list,form',
            'target': 'current',
            'domain': [('ai_coworker_id', '=', self.id)],
        }

    def get_available_skills(self):
        """Return all skills available to this quest.

        Collects skills from:
        0. Quest-level skills (pipeline/orchestration — HIGHEST priority)
        1. Quest's agents (ai.coworker.agent → ai.agent.skill_ids)
        2. Quest's identity (ai.coworker.identity_id.skill_ids)
        3. Quest-specific skill copies (ai.coworker.skill)

        Returns a list of dicts with name, description, trigger_keywords,
        category, recipe_text, and source.
        """
        self.ensure_one()
        skills = {}  # keyed by name to deduplicate

        # 0. Quest-level skills (pipeline/orchestration — highest priority)
        for skill in self.skill_ids:
            skills[skill.name] = {
                'name': skill.name,
                'description': skill.description or '',
                'trigger_keywords': skill.trigger_keywords or '',
                'category': skill.category or 'general',
                'recipe_text': skill.recipe_text or '',
                'source': 'quest',
                'priority': 'high',
            }

        # 1. Skills from agents
        for rel in self.agent_ids:
            agent = rel.agent_id
            for skill in agent.skill_ids:
                if skill.name not in skills:
                    skills[skill.name] = {
                        'name': skill.name,
                        'description': skill.description or '',
                        'trigger_keywords': skill.trigger_keywords or '',
                        'category': skill.category or 'general',
                        'recipe_text': skill.recipe_text or '',
                        'source': 'agent',
                    }

        # 2. Skills from identity
        if self.identity_id:
            for skill in self.identity_id.skill_ids:
                if skill.name not in skills:
                    skills[skill.name] = {
                        'name': skill.name,
                        'description': skill.description or '',
                        'trigger_keywords': skill.trigger_keywords or '',
                        'category': skill.category or 'general',
                        'recipe_text': skill.recipe_text or '',
                        'source': 'identity',
                    }

        # 3. Quest-specific skill copies (override shared with quest version)
        for copy in self.skill_copy_ids:
            name = copy.name
            skills[name] = {
                'name': name,
                'description': copy.description or '',
                'trigger_keywords': copy.trigger_keywords or '',
                'category': 'quest',
                'recipe_text': copy.recipe_text or '',
                'source': 'quest_copy',
            }

        return list(skills.values())

    def action_monthly_overview(self):
        """Smart button: show this month's session lines with systemtoken breakdown."""
        self.ensure_one()
        from datetime import date
        today = date.today()
        month_start = date(today.year, today.month, 1)
        return {
            'name': f'Förbrukning — {self.name}',
            'type': 'ir.actions.act_window',
            'res_model': 'ai.coworker.session.line',
            'view_mode': 'list,pivot',
            'target': 'current',
            'domain': [
                ('session_id.coworker_id', '=', self.id),
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
            'coworker_id': self.id,
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

    def _trigger_watch(self, records):
        """Anropas av base_automation när bevakad data ändras.

        Hämtar watch-init_type:en, filtrerar records på watch_domain och
        kör medarbetaren med den ändrade posten som kontext.
        """
        self.ensure_one()
        if not records:
            return
        watch = self._get_active_init('watch')
        if not watch:
            _logger.warning('_trigger_watch: ingen aktiv watch-init för %s',
                            self.name)
            return
        # Filtrera på watch_domain ("Apply on" — Odoo 18 base_automation
        # stödjer inte filter_domain för on_create_or_write, så vi filtrerar här)
        filtered = records
        if watch.watch_domain:
            try:
                from odoo.tools.safe_eval import safe_eval
                filtered = records.filtered_domain(
                    safe_eval(watch.watch_domain))
            except Exception as e:
                _logger.warning('watch_domain-filtrering misslyckades: %s', e)
        if not filtered:
            return
        # Budget check
        try:
            _warn, exhausted = self.check_cap()
            if exhausted:
                _logger.info('Watch %s skippad: budget slut', self.name)
                return
        except Exception:
            pass
        for record in filtered[:3]:
            try:
                session = self.env['ai.coworker.session'].create({
                    'coworker_id': self.id,
                    'name': f'Watch: {record._name} {record.id}',
                    'status': 'active',
                    'user_id': self.env.ref('base.user_root').id,
                })
                prompt = (
                    f'En dataändring upptäcktes på record '
                    f'{record.display_name or record.id} ({record._name}).\n'
                    f'Granska recordet och agera lämpligt.\n'
                )
                self.with_context(_ai_context_model=record._name,
                                  _ai_context_id=record.id).run(prompt)
            except Exception as e:
                _logger.warning('Watch-körning misslyckades: %s', e)

    def action_run_scheduled(self):
        """Run this quest as a scheduled automation.

        Called from ir.cron. Uses standing rules (auto_allowed_tools,
        auto_allowed_commands) for auto-approval. Sends completion
        notification if notify_on_completion is set.

        Returns:
            dict with status, result_text, error if any
        """
        self.ensure_one()
        _logger.info('Scheduled run starting for quest: %s (run #%d)',
                     self.name, self.run_count + 1)

        # Parse standing rules
        allowed_tools = []
        if self.auto_allowed_tools:
            try:
                allowed_tools = json.loads(self.auto_allowed_tools)
                if not isinstance(allowed_tools, list):
                    allowed_tools = []
            except (json.JSONDecodeError, TypeError):
                _logger.warning('Invalid auto_allowed_tools JSON for quest %s', self.name)
                allowed_tools = []

        allowed_commands = []
        if self.auto_allowed_commands:
            try:
                allowed_commands = json.loads(self.auto_allowed_commands)
                if not isinstance(allowed_commands, list):
                    allowed_commands = []
            except (json.JSONDecodeError, TypeError):
                _logger.warning('Invalid auto_allowed_commands JSON for quest %s', self.name)
                allowed_commands = []

        # Build system prompt
        system_prompt = self.description or ''
        if self.identity_id:
            system_prompt = self.identity_id.system_prompt or system_prompt

        # Get model from first agent or default
        model = 'cerebras/gpt-oss-120b'
        for agent_rel in self.agent_ids:
            if agent_rel.agent_id.model_id:
                model = agent_rel.agent_id.model_id.name
                break

        try:
            # Create session
            session = self.env['ai.coworker.session'].create({
                'coworker_id': self.id,
                'status': 'active',
                'user_id': self.env.ref('base.user_root').id
                          if self.env.ref('base.user_root', raise_if_not_found=False)
                          else 1,
            })

            import asyncio
            from odoo.addons.ai_agent_core.core.provider import ProviderFactory, BifrostProvider
            from odoo.addons.ai_agent_core.core.tools import (
                ToolRegistry, builtin_tools, planning_tools, TodoList,
                wrap_tools_with_env,
            )
            from odoo.addons.ai_agent_core.core.loop import AgentLoop, AgentConfig
            from odoo.addons.ai_agent_core.core.interrupt import AutoInterruptHandler
            from odoo.addons.ai_agent_core.core.permission import (
                PermissionEngine, PermissionMode,
            )

            provider_instance, provider_model = ProviderFactory.from_coworker(self)
            provider = provider_instance or BifrostProvider(
                base_url='http://192.168.11.150:8080/v1',
                virtual_key='opencode',
            )
            tools = ToolRegistry()
            tools.register_many(wrap_tools_with_env(builtin_tools(), self.env))

            # Add planning tools
            todo = TodoList()
            for pt in planning_tools(todo):
                tools.register(pt)

            # Set up permission engine with AUTO mode + standing rules
            permissions = PermissionEngine(mode=PermissionMode.AUTO)
            for entry in allowed_tools:
                entry = entry.strip()
                if ' ' in entry:
                    tool_name, target = entry.split(' ', 1)
                    permissions.add_task_rule(tool_name.strip(), target.strip())
                else:
                    permissions.auto_allow_tools.add(entry)
            for cmd in allowed_commands:
                permissions.session_allow_commands.add(cmd.strip())

            # Use auto-interrupt handler (never blocks)
            interrupt = AutoInterruptHandler()

            loop = self._build_loop(
                provider=provider, tools=tools,
                model=model, system_prompt=system_prompt, max_rounds=10,
            )
            loop.interrupt_handler = interrupt
            loop.permission_engine = permissions

            def _record_denial_as_suggestion(tool_name, args, reason):
                """Async-yta (cron): nekade hårda stopp → workspace-approval-kö."""
                try:
                    if 'workspace.activity.suggestion' not in self.env:
                        return
                    target_user = self.user_id or session.user_id
                    self.env['workspace.activity.suggestion']._create_suggestion(
                        summary=f"Godkänn krävs: {tool_name}",
                        detail=(
                            f"Medarbetaren {self.name} ville köra {tool_name} "
                            f"med {args} men nekades ({reason}). "
                            f"Utför åtgärden manuellt eller via godkännande."
                        ),
                        suggestion_type='mail.activity',
                        source='coworker',
                        user=target_user,
                        coworker_id=self.id,
                        session_id=session.id if session else None,
                    )
                    _logger.info(
                        'Rekorderade nekad %s som workspace-förslag för %s',
                        tool_name, self.name)
                except Exception as e:
                    _logger.warning('Denial→suggestion misslyckades: %s', e)

            loop.denial_callback = _record_denial_as_suggestion

            async def _run():
                prompt = (
                    f"You are an automated agent. Your task:\n\n{self.description}"
                    if self.description else
                    "Execute the scheduled task. Be thorough and complete."
                )
                return await loop.run(prompt)

            loop_obj = asyncio.new_event_loop()
            asyncio.set_event_loop(loop_obj)
            try:
                response = loop_obj.run_until_complete(_run())
            finally:
                loop_obj.close()

            result_text = response.text if hasattr(response, 'text') else str(response)

            # Update quest stats
            self.write({
                'last_run': fields.Datetime.now(),
                'last_status': 'ok',
                'run_count': self.run_count + 1,
                'status': 'active',
            })

            # Update session
            session.write({
                'status': 'done',
                'result': result_text[:2000] if result_text else '',
            })

            # Send completion notification
            if self.notify_on_completion and self.notify_target:
                self._send_completion_notification(result_text, 'ok')

            _logger.info('Scheduled run completed for quest: %s', self.name)
            return {
                'status': 'ok',
                'result_text': result_text,
                'run_count': self.run_count,
            }

        except Exception as e:
            _logger.error('Scheduled run failed for quest %s: %s', self.name, e,
                         exc_info=True)
            self.write({
                'last_run': fields.Datetime.now(),
                'last_status': 'error',
                'run_count': self.run_count + 1,
            })
            if self.notify_on_completion and self.notify_target:
                self._send_completion_notification(str(e), 'error')
            return {
                'status': 'error',
                'error': str(e),
            }

    def _send_completion_notification(self, result_text, status):
        """Send completion notification to the notify_target channel."""
        self.ensure_one()
        if not self.notify_target:
            return
        try:
            icon = '✅' if status == 'ok' else '❌'
            msg = (
                f'{icon} **{self.name}** — scheduled run completed.\n'
                f'Status: {status.upper()}\n'
                f'Run #{self.run_count}\n\n'
                f'{result_text[:1000]}'
            )
            # Try posting to a discuss channel by ID or name
            target = self.notify_target.strip()
            if target.isdigit():
                channel = self.env['discuss.channel'].browse(int(target))
                if channel.exists():
                    channel.message_post(body=msg, message_type='notification')
            else:
                # Post via quest's own message_post (mail.thread)
                self.message_post(body=msg, message_type='notification')
        except Exception as e:
            _logger.warning('Failed to send completion notification: %s', e)

    def _tool_access_group_ids(self, session=None, access_user=None):
        """Effective Odoo group ids that gate tool access (tool-access-groups).

        - access_user explicit → den användarens grupper
        - session med riktig (icke-superuser, icke-public) användare →
          session.user_id.groups_id
        - annars (cron/webhook/mail, sudo-skapade sessioner) → coworkerns
          egna group_ids (icke-interaktiv policy)
        """
        self.ensure_one()
        if access_user:
            return access_user.groups_id.ids
        u = session and session.user_id
        if (u and u.id != SUPERUSER_ID
                and (u.has_group('base.group_user')
                     or u.has_group('base.group_portal'))):
            return u.groups_id.ids
        return self.group_ids.ids

    def run(self, prompt, system_prompt=None, force_model=None,
            force_agent=None, session=None):
        """Run quest synchronously and return AI response text.

        Designed for bridge integrations (html_editor, mail, webhook, etc.)
        where a simple prompt→response flow is needed.

        Args:
            prompt: The user prompt to send
            system_prompt: Optional override for system prompt
            force_model: Optional model override (buzz agentens egen modell, 7.5)
            force_agent: Optional ai.agent — kör med agentens EGNA
                         skills + tools (identity-bound ai.tool, 7.5)
            session: Optional existing ai.coworker.session to reuse
                     (webhook flow keeps its own event-tracked session);
                     a new session is created when omitted.

        Returns:
            str: AI response text (plain text, no markdown rendering)
        """
        self.ensure_one()

        # Resolve model — force_model (7.5) → agent-modell → standard
        model = force_model or 'cerebras/gpt-oss-120b'
        if not force_model:
            for qa in self.agent_ids:
                agent = qa.agent_id
                if agent.model_id and agent.model_id.name:
                    model = agent.model_id.name
                    break

        # Build system prompt
        if system_prompt is None:
            system_prompt = self.description or ''
            if self.identity_id:
                system_prompt = self.identity_id.system_prompt or system_prompt

        # Agentens EGNA skills i systemprompten (7.5)
        if force_agent and force_agent.skill_ids:
            skill_ctx = '\n\n## Agent Skills\n' + '\n'.join(
                f'### {s.name}\n{s.recipe_text or s.description or ""}'
                for s in force_agent.skill_ids)
            system_prompt = (system_prompt or '') + skill_ctx

        # Create session for tracking (reuse provided session when given)
        session = session or self.env['ai.coworker.session'].create({
            'coworker_id': self.id,
            'status': 'active',
            'user_id': self.env.user.id,
            'name': prompt[:80] if prompt else 'Quest run',
        })

        try:
            import asyncio
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                from odoo.addons.ai_agent_core.core.provider import ProviderFactory, BifrostProvider
                from odoo.addons.ai_agent_core.core.tools import (
                    ToolRegistry, builtin_tools, wrap_tools_with_env,
                )
                from odoo.addons.ai_agent_core.core.loop import AgentLoop, AgentConfig

                provider_instance, provider_model = ProviderFactory.from_coworker(self)
                provider = provider_instance or BifrostProvider(
                    base_url='http://192.168.11.150:8080/v1',
                    virtual_key='opencode',
                )
                tools = ToolRegistry()
                tools.register_many(wrap_tools_with_env(builtin_tools(), self.env))

                # Access-grupper (tool-access-groups): användarens grupper
                # styr vilka verktyg som serialiseras — LLM:en ser ALDRIG
                # otillåtna verktyg (och de kostar inga tokens).
                tool_access_groups = self._tool_access_group_ids(session=session)

                # Agentens EGNA tools (7.5): identity-bundna ai.tool-record
                if force_agent and force_agent.identity_id \
                        and force_agent.identity_id.tool_ids:
                    from odoo.addons.ai_agent_core.core.tools import (
                        ai_tool_records_to_tools)
                    identity_tools = force_agent.identity_id.tool_ids
                    tools.register_many(ai_tool_records_to_tools(
                        identity_tools._filter_by_access_groups(
                            tool_access_groups), self.env))

                # Coworkerns EGNA tools (D5 drift): ai.tool-poster kopplade
                # via coworker_ids — tillgängliga i run() för alla initeringar
                # (openai_api, webhook, web_ui, cron …)
                custom_tools = self.tool_ids.filtered(
                    lambda t: t.active)._filter_by_access_groups(
                        tool_access_groups)
                if custom_tools:
                    from odoo.addons.ai_agent_core.core.tools import (
                        ai_tool_records_to_tools)
                    tools.register_many(ai_tool_records_to_tools(
                        custom_tools, self.env))

                # Förmågeserialisering (spec 3.3/3.4): flat/enum/namespace per
                # coworker. Medlemmarna är redan access-filtrerade (registret
                # innehåller bara tillåtna verktyg) — otillåtna medlemmar
                # saknas både som tool och som operation (spec 3.5).
                cap_prompt_suffix = ''
                if self.serialize_capabilities != 'flat':
                    from odoo.addons.ai_agent_core.core.tools import (
                        apply_capability_serialization, _sanitize_tool_name)
                    cap_recs = self.env['ai.tool.capability'].search([
                        ('active', '=', True)])
                    capabilities = []
                    for cap in cap_recs:
                        member_names = [
                            _sanitize_tool_name(t.name)
                            for t in cap.member_ids
                            if _sanitize_tool_name(t.name) in tools]
                        if member_names:
                            capabilities.append({
                                'name': cap.name,
                                'description': cap.description,
                                'member_names': member_names,
                            })
                    cap_prompt_suffix = apply_capability_serialization(
                        tools, capabilities, self.serialize_capabilities)
                    if cap_prompt_suffix:
                        system_prompt = (system_prompt or '') + cap_prompt_suffix

                # Lineage: gör sessionen känd för OKF-injektionen så att
                # concept_injected-edges loggas (ai_lineage_session_id).
                self = self.with_context(ai_lineage_session_id=session.id)

                loop_obj = self._build_loop(
                    provider=provider, tools=tools,
                    model=model, system_prompt=system_prompt, max_rounds=10,
                )

                # PermissionEngine får användarens grupper (defense-in-depth):
                # anrop på andra vägar till gruppbundna verktyg nekas även om
                # registreringsfiltret missat något (tool-access-groups 1.4).
                if hasattr(loop_obj, 'permissions'):
                    loop_obj.permissions.user_group_ids = set(tool_access_groups)

                # Mail/webhook-flöden (tillitsfulla automatiska kontexter):
                # kör med AUTO-mode så odoo_create/odoo_write/odoo_call_method
                # inte fastnar i HITL-kön. Endast när context flaggan sätts av
                # initierings-koden (aldrig från chat).
                if self.env.context.get('_ai_auto_approve'):
                    from odoo.addons.ai_agent_core.core.permission import (
                        PermissionEngine, PermissionMode)
                    loop_obj.permissions = PermissionEngine(
                        mode=PermissionMode.AUTO)
                    loop_obj.permissions.user_group_ids = set(tool_access_groups)

                async def _run():
                    return await loop_obj.run(prompt)

                response = loop.run_until_complete(_run())
            finally:
                loop.close()

            result_text = response.text if hasattr(response, 'text') else str(response)

            # Save session line with token tracking
            input_t = getattr(response, 'input_tokens', 0)
            output_t = getattr(response, 'output_tokens', 0)
            model_real = getattr(response, 'model', '')
            sys_mult = 1.0
            if model_real:
                ai_model = self.env['ai.model'].search(
                    [('name', 'ilike', model_real)], limit=1)
                if ai_model:
                    sys_mult = ai_model.sys_multiplier

            self.env['ai.coworker.session.line'].create({
                'session_id': session.id,
                'role': 'user',
                'content': prompt[:2000] if prompt else '',
                'sequence': 1,
            })
            self.env['ai.coworker.session.line'].create({
                'session_id': session.id,
                'role': 'assistant',
                'content': result_text,
                'token_input': input_t,
                'token_output': output_t,
                'model_real': model_real,
                'sys_multiplier': sys_mult,
                'sequence': 2,
            })
            # Persist tool executions recorded by the loop (observability
            # + lets tests assert expect_tools via session lines)
            for i, (t_name, t_preview) in enumerate(
                    getattr(loop_obj, 'tool_history', [])):
                self.env['ai.coworker.session.line'].create({
                    'session_id': session.id,
                    'role': 'tool',
                    'tool_name': t_name,
                    'content': t_preview,
                    'sequence': 10 + i,
                })

            # Update session and quest totals
            session.write({
                'token_input': session.token_input + input_t,
                'token_output': session.token_output + output_t,
                'status': 'done',
            })

            self.total_input_tokens += input_t
            self.total_output_tokens += output_t
            self.total_sys_tokens += int((input_t + output_t) * sys_mult)

            _logger.info('Quest run completed: %s (%d in / %d out tokens)',
                        self.name, input_t, output_t)

            return result_text

        except Exception as e:
            _logger.error('Quest run failed: %s', e, exc_info=True)
            session.write({'status': 'error'})
            return f'Error: {str(e)}'

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

        # Init-type-scoping (odoo-model-tools change 2.2): powerbox begränsas
        # till model_ids-bundna modeller + aktuell rekordkontext.
        scoped = self._visible_models('powerbox')
        ctx = dict(self.env.context)
        if res_model:
            scoped = set(scoped or ()) | {res_model}
        if scoped:
            ctx['_ai_scoped_models'] = scoped
        self = self.with_context(**ctx)

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
        session = self.env['ai.coworker.session'].create({
            'coworker_id': self.id,
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
                from odoo.addons.ai_agent_core.core.provider import ProviderFactory, BifrostProvider
                from odoo.addons.ai_agent_core.core.tools import (
                    ToolRegistry, builtin_tools, wrap_tools_with_env,
                )
                from odoo.addons.ai_agent_core.core.loop import AgentLoop, AgentConfig

                provider_instance, provider_model = ProviderFactory.from_coworker(self)
                provider = provider_instance or BifrostProvider(
                    base_url='http://192.168.11.150:8080/v1',
                    virtual_key='opencode',
                )
                tools = ToolRegistry()
                tools.register_many(wrap_tools_with_env(builtin_tools(), self.env))

                loop_obj = self._build_loop(
                    provider=provider, tools=tools,
                    model=model, system_prompt=system_prompt, max_rounds=5,
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

                self.env['ai.coworker.session.line'].create({
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


    def write(self, vals):
        res = super(AICoworker, self).write(vals)
        if any(k in vals for k in ('orchestration_mode', 'channel_id', 'is_supervisor')):
            self._sync_buzz_agents_to_channel()
        return res

    def _sync_buzz_agents_to_channel(self):
        """Sync agent partners to channel when buzz mode or channel changes."""
        for quest in self:
            if quest._get_effective_orchestration_mode() != 'buzz' or not quest.channel_id:
                continue
            quest.channel_id.ai_coworker_id = quest.id
            for rel in quest.agent_ids:
                rel.agent_id._ensure_partner()
            agent_ids = quest.agent_ids.mapped('agent_id').ids
            quest.channel_id.ai_agent_ids = [(6, 0, agent_ids)]
            quest.channel_id._sync_ai_agent_members()

    @api.onchange('orchestration_mode')
    def _onchange_orchestration_mode(self):
        """Keep legacy is_supervisor in sync."""
        if self.orchestration_mode == 'supervisor':
            self.is_supervisor = True
        elif self.orchestration_mode and self.orchestration_mode != 'single':
            self.is_supervisor = False

    # ── Org integration fields (ai-org-onboarding) ──

    is_default = fields.Boolean(
        'Default Coworker', default=False,
        help='Systemets allmänna coworker — den som fanns från installation.')

    employee_id = fields.Many2one(
        'hr.employee', string='HR Employee',
        help='Motswarande hr.employee för denna AI-coworker. '
             'Gör att AI:n syns i personalvyn och kan vara chef.')

    heartbeat_enabled = fields.Boolean(
        'Heartbeat Active', default=True,
        help='När True vaknar coworkern regelbundet för att checka '
             'budget, tasks, mål och nudge-behov.')

    department_id = fields.Many2one(
        'hr.department', string='Department',
        help='Avdelningen som denna AI-medarbetare tillhör.')

    # ── Heartbeat ──

    def _heartbeat(self):
        """Single heartbeat tick: budget → tasks → goals → nudge.

        Called periodically by _heartbeat_all().
        Each coworker decides what to do based on current state.
        """
        self.ensure_one()

        # 1. Check budget
        warning, exhausted = self.check_cap()
        if exhausted:
            return  # No budget — sleep

        # 2. Check for pending tasks
        pending_tasks = self.env['ai.org.task'].search([
            ('coworker_id', '=', self.id),
            ('status', '=', 'todo'),
            ('checkout_lock', '=', False),
        ], order='priority desc, create_date asc', limit=1)

        if pending_tasks:
            # Check out and work on the task
            _logger.info('Heartbeat %s: found pending task %s, checking out',
                        self.name, pending_tasks.name)
            task = pending_tasks[0]
            task.action_checkout()
            return

        # 3. Check goals — proactive work
        active_goals = self.env['ai.org.goal'].search([
            ('coworker_id', '=', self.id),
            ('status', '=', 'active'),
            ('progress', '<', 100.0),
        ], limit=1)

        if active_goals:
            _logger.info('Heartbeat %s: working on goal %s',
                        self.name, active_goals.name)
            # Create a session for proactive goal work
            self.env['ai.coworker.session'].create({
                'coworker_id': self.id,
                'name': f'Goal: {active_goals.name[:50]}',
                'status': 'active',
            })
            return

        # 4. Nudge? (handled by kaizen/onboard separately)
        # 5. Sleep until next heartbeat

    @api.model
    def _heartbeat_all(self):
        """Called by ir.cron — iterate all active coworkers."""
        from datetime import datetime, timedelta

        # Check system setting
        icp = self.env['ir.config_parameter'].sudo()
        enabled = icp.get_param('ai_agent_core.heartbeat_enabled', 'True')
        if enabled != 'True':
            return

        interval = int(icp.get_param('ai_agent_core.heartbeat_interval', '5'))

        coworkers = self.search([
            ('active', '=', True),
            ('status', '=', 'active'),
            ('heartbeat_enabled', '=', True),
        ])

        now = datetime.now()
        for coworker in coworkers:
            try:
                # Check if enough time since last heartbeat
                if coworker.last_heartbeat:
                    last = coworker.last_heartbeat
                    if last and isinstance(last, datetime):
                        diff = (now - last).total_seconds() / 60
                        if diff < interval:
                            continue

                coworker._heartbeat()
                coworker.write({'last_heartbeat': now})
            except Exception as e:
                _logger.error('Heartbeat failed for %s: %s',
                            coworker.name, e)

    last_heartbeat = fields.Datetime('Last Heartbeat')

    # ── Employee link ──

    def _ensure_employee(self):
        """Create or return hr.employee(is_ai=True) for this coworker.

        Called automatically on create if not already linked.
        Ensures AI coworkers appear in hr.employee views
        alongside human employees.
        """
        self.ensure_one()
        if self.employee_id:
            return self.employee_id

        if not self.name:
            return False

        alias = (self.channel_alias or self.name).lower().replace(' ', '-')[:20]
        employee = self.env['hr.employee'].create({
            'name': self.name,
            'work_email': f'ai-{alias}-{self.id}@ai.internal',
            'is_ai': True,
            'ai_coworker_id': self.id,
            'department_id': self.department_id.id if self.department_id else False,
        })
        self.write({'employee_id': employee.id})
        _logger.info('Created hr.employee %s for coworker %s',
                    employee.name, self.name)
        return employee

    @api.model_create_multi
    def create(self, vals_list):
        records = super(AICoworker, self).create(vals_list)
        for record, vals in zip(records, vals_list):
            # Seed alla init_type-rader (en per typ) så UI:t
            # (many2many_checkboxes) kan visa varje typ som kryssruta.
            try:
                record._ensure_all_init_types()
            except Exception as e:
                _logger.warning('Could not seed init types for %s: %s',
                              record.name, e)
            # Auto-create hr.employee for new coworkers (inte default)
            if not vals.get('employee_id') and not vals.get('is_default'):
                try:
                    record._ensure_employee()
                except Exception as e:
                    _logger.warning('Could not create employee for %s: %s',
                                  record.name, e)
        return records


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def _send_mail_reply(mail_message, reply_text, quest):
    """Send AI response as email reply."""
    try:
        if not mail_message.email_from:
            return
        mail_values = {
            'subject': f'Re: {mail_message.subject or "AI Response"}',
            'body_html': f'<pre>{reply_text}</pre>',
            'email_to': mail_message.email_from,
            'email_from': quest.company_id.email or 'ai@vertel.se',
            'reply_to': mail_message.message_id,
        }
        mail = quest.env['mail.mail'].create(mail_values)
        mail.send()
        _logger.info('AI reply sent to %s for quest %s',
                    mail_message.email_from, quest.name)
    except Exception as e:
        _logger.warning('Failed to send AI reply: %s', e)


def _extract_text(filename, content):
    """Extract text from uploaded file. Supports PDF, DOCX, TXT, etc."""
    ext = filename.lower().split('.')[-1] if '.' in filename else ''
    if ext in ('txt', 'md', 'csv', 'py', 'js', 'html', 'xml', 'json', 'yml', 'yaml'):
        try:
            return content.decode('utf-8')
        except UnicodeDecodeError:
            return content.decode('utf-8', errors='replace')
    elif ext == 'pdf':
        try:
            import io, PyPDF2
            reader = PyPDF2.PdfReader(io.BytesIO(content))
            return '\n'.join(p.extract_text() or '' for p in reader.pages)
        except Exception as e:
            return f'[PDF: {e}]'
    elif ext == 'docx':
        try:
            import io, docx
            doc = docx.Document(io.BytesIO(content))
            return '\n'.join(p.text for p in doc.paragraphs if p.text.strip())
        except Exception as e:
            return f'[DOCX: {e}]'
    try:
        return content.decode('utf-8')
    except Exception:
        return f'[Binary: {len(content)} bytes]'


class AICoworkerMonthlySummary(models.Model):
    """Monthly systemtoken summary for billing and reporting (T3.5)."""
    _name = 'ai.coworker.monthly_summary'
    _description = 'Monthly Quest Summary'
    _order = 'month desc, coworker_id asc'
    _rec_name = 'display_name'

    coworker_id = fields.Many2one('ai.coworker', required=True, ondelete='cascade',
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

    @api.depends('coworker_id.name', 'month')
    def _compute_display_name(self):
        for r in self:
            quest_name = r.coworker_id.name if r.coworker_id else '?'
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

        quests = self.env['ai.coworker'].search([('status', '=', 'active')])
        created = 0
        for quest in quests:
            # Check if summary already exists
            existing = self.search([
                ('coworker_id', '=', quest.id),
                ('month', '=', month),
            ], limit=1)
            if existing:
                continue

            # Aggregate session lines for this month
            lines = self.env['ai.coworker.session.line'].search([
                ('session_id.coworker_id', '=', quest.id),
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
                'coworker_id': quest.id,
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


class AICoworkerAgent(models.Model):
    _name = 'ai.coworker.agent'
    _description = 'Quest Agent Assignment'
    _order = 'sequence asc'

    coworker_id = fields.Many2one('ai.coworker', required=True, ondelete='cascade')
    agent_id = fields.Many2one('ai.agent', required=True, string='Agent')
    sequence = fields.Integer(default=10)
    role = fields.Selection([
        ('member', 'Member'),
        ('leader', 'Leader'),
        ('observer', 'Observer'),
        ('on_demand', 'On Demand'),
    ], default='member', string='Role',
        help='Role of this agent within the quest.')
    is_auto_created = fields.Boolean(
        'Auto-created', default=False,
        help='True if this agent was created automatically by the quest.')

    # ── Hårda datablock (agent-memory-governance: per-par) ──
    block_personal = fields.Boolean(
        'Block personligt', default=False,
        help='Hårt block: personligt minne når ALDRIG denna agent (i denna '
             'AI Medarbetare). Ej förhandlingsbart av supervisor.')
    block_company = fields.Boolean('Block företag', default=False)
    block_coworker = fields.Boolean('Block medarbetarminne', default=False)
    level_personal = fields.Selection(
        [('L0', 'L0'), ('L1', 'L1'), ('L2', 'L2'), ('L3', 'L3')],
        string='Nivå personligt', help='Tom = ärv från AI Medarbetaren.')
    level_company = fields.Selection(
        [('L0', 'L0'), ('L1', 'L1'), ('L2', 'L2'), ('L3', 'L3')],
        string='Nivå företag', help='Tom = ärv från AI Medarbetaren.')
    level_coworker = fields.Selection(
        [('L0', 'L0'), ('L1', 'L1'), ('L2', 'L2'), ('L3', 'L3')],
        string='Nivå medarbetare', help='Tom = ärv från AI Medarbetaren.')

    def _effective_level(self, scope):
        """Kopplingens effektiva nivå för scope (tom = ärv från coworker)."""
        level_field = 'level_%s' % scope
        own = getattr(self, level_field, None)
        if own:
            return own
        return self.coworker_id.memory_level or 'L1'

    def _blocked(self, scope):
        """True om scopen är hårt blockerad för denna koppling."""
        return bool(getattr(self, 'block_%s' % scope, False))
    orchestration_mode = fields.Selection(
        related='coworker_id.orchestration_mode', string='Orchestration Mode',
        store=False, readonly=True,
        help='Ärvd från coworkern — för att dölja roll-kolumnen i team-lägen.')

    # Display fields for the coworker form Agents tab (like legacy ai_agent)
    agent_model_id = fields.Many2one(
        'ai.model', compute='_compute_agent_model_id', string='Model', store=False,
        help='Agent model shown on the assignment row.')
    agent_tools_display = fields.Char(
        'Tools', compute='_compute_agent_display', store=False,
        help='Comma-separated tool names of the assigned agent.')
    agent_memories_display = fields.Char(
        'Memories', compute='_compute_agent_display', store=False,
        help='Comma-separated memory names of the assigned agent.')

    @api.depends('agent_id.model_id')
    def _compute_agent_model_id(self):
        for rec in self:
            rec.agent_model_id = rec.agent_id.model_id

    @api.depends('agent_id.tool_ids.name', 'agent_id.memory_ids.memory_id.name',
                 'agent_id.rag_memory_ids.name')
    def _compute_agent_display(self):
        for rec in self:
            tools = rec.agent_id.tool_ids.mapped('name')
            memories = (
                rec.agent_id.memory_ids.mapped('memory_id.name')
                + rec.agent_id.rag_memory_ids.mapped('name')
            )
            rec.agent_tools_display = ', '.join([t for t in tools if t])
            rec.agent_memories_display = ', '.join([m for m in memories if m])

    @api.model_create_multi
    def create(self, vals_list):
        records = super(AICoworkerAgent, self).create(vals_list)
        for rec in records:
            quest = rec.coworker_id
            if quest._get_effective_orchestration_mode() == 'buzz' and quest.channel_id:
                rec.agent_id._ensure_partner()
                quest.channel_id.ai_agent_ids = [(4, rec.agent_id.id)]
                quest.channel_id._sync_ai_agent_members()
        return records

    def unlink(self):
        for rec in self:
            quest = rec.coworker_id
            agent = rec.agent_id
            if quest.channel_id and agent in quest.channel_id.ai_agent_ids:
                quest.channel_id.ai_agent_ids = [(3, agent.id)]
                # Remove partner from channel members if not used by another buzz quest in same channel
                if agent.partner_id:
                    other_buzz_assignments = self.search([
                        ('agent_id', '=', agent.id),
                        ('coworker_id.channel_id', '=', quest.channel_id.id),
                        ('id', '!=', rec.id),
                    ])
                    if not other_buzz_assignments:
                        member = self.env['discuss.channel.member'].sudo().search([
                            ('channel_id', '=', quest.channel_id.id),
                            ('partner_id', '=', agent.partner_id.id),
                        ], limit=1)
                        if member:
                            member.unlink()
        return super(AICoworkerAgent, self).unlink()

    def action_dismiss_auto_agent(self):
        """Remove an auto-created agent from this quest and delete it if unused."""
        for rec in self:
            if not rec.is_auto_created:
                raise UserError(_('Only auto-created agents can be dismissed.'))
            agent = rec.agent_id
            rec.unlink()
            # Delete agent if it has no other quest assignments
            if not self.env['ai.coworker.agent'].search_count([('agent_id', '=', agent.id)]):
                agent.sudo().unlink()
        return {'type': 'ir.actions.act_window_close'}
