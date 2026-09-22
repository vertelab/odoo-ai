# -*- coding: utf-8 -*-
"""ai.agent — standalone with identity, skills, tools, memories, provider, budget."""

import base64
import logging
import requests
from odoo import models, fields, api, _
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)


class AIAgent(models.Model):
    _name = 'ai.agent'
    _description = 'AI Agent'
    _order = 'name asc'

    name = fields.Char(required=True)
    active = fields.Boolean(default=True)
    description = fields.Text(help='What this agent does — used by supervisor router')
    sequence = fields.Integer(default=10)

    # Role/Goal/Backstory (for supervisor orchestration)
    ai_role = fields.Char('Role', help='e.g. "Financial Analyst", "Code Reviewer"')
    ai_goal = fields.Text('Goal', help='What this agent aims to accomplish')
    ai_backstory = fields.Text('Backstory', help='Background context for the agent')

    # Identity
    identity_id = fields.Many2one('ai.identity', string='Identity',
                                   help='What personality/soul this agent has')

    # Kanban images (related for efficient kanban display)
    partner_image_128 = fields.Binary(related='partner_id.image_128',
                                       string='Partner Image',
                                       help='Avatar from channel partner')

    # ── Buzz workspace membership ──
    partner_id = fields.Many2one('res.partner', string='Channel Partner',
        help='Automatically created when this agent is used in a Buzz workspace. '
             'Enables the agent to be a visible member of a Discuss channel.')
    alias_name = fields.Char('Alias',
        help='Short name for @mentions, e.g. "magnus".')
    trigger_words = fields.Char('Trigger Words',
        help='Comma-separated keywords used for routing in Buzz workspaces.')
    is_buzz_active = fields.Boolean(compute='_compute_is_buzz_active',
        help='True if this agent is assigned to at least one Buzz workspace.')

    # Skills (task-specific — not pipeline/orchestration)
    skill_ids = fields.Many2many('ai.skill', 'ai_agent_skill_rel',
                                  'agent_id', 'skill_id', string='Skills',
                                  help='Task-specific skills this agent can perform')

    # Tools — M2M till globala ai.tool-record
    tool_ids = fields.Many2many(
        'ai.tool', 'ai_agent_tool_custom_rel',
        'agent_id', 'tool_id', string='Tools',
        help='Globala ai.tool-record kopplade till denna agent. '
             'Lagra flera tools per agent. Speglar ai.tool.agent_ids.',
    )

    # Memories (FAISS/pgvector RAG)
    memory_ids = fields.One2many('ai.agent.memory', 'agent_id', string='Skill Memories',
                                  help='Skill-specific memories linked to this agent')
    rag_memory_ids = fields.One2many('ai.memory', 'agent_id', string='RAG Knowledge',
                                      help='Permanent FAISS/pgvector knowledge bases (handbooks, websites)')

    # Model (points to ai.model — handles both Bifrost and Direct)
    model_id = fields.Many2one('ai.model', string='Model',
                                help='The LLM this agent uses. Can be Bifrost or Direct.')
    model_name = fields.Char(related='model_id.name', readonly=True, store=True)

    # Config
    temperature = fields.Float(default=0.7)
    max_tokens = fields.Integer(default=4096)
    max_rounds = fields.Integer(default=10)

    # Runtime (external-agent-runtime D1/D2): ortogonal axel mot init-typen.
    # Init-typen säger VEM som väcker coworkern; runtime säger VAR loopen kör.
    # Ingen migration behövs — default täcker befintliga rader.
    runtime = fields.Selection([
        ('in_process', 'I Odoo-processen'),
        ('external', 'Extern process'),
    ], string='Körmiljö', default='in_process', required=True,
        help='Var agent-loopen kör. "I Odoo-processen" ockuperar en '
             'Odoo-worker under körningen; "Extern process" startar '
             'agenten som en separat process och frigör workern direkt.')

    # Budget (budget-hard-cap D8: budget_limit/budget_used i USD begravda —
    # systemtokens är enda valuta, hanteras på ai.coworker)

    # Status
    status = fields.Selection([
        ('draft', 'Draft'), ('active', 'Active'), ('error', 'Error'),
    ], default='draft')
    last_run = fields.Datetime()

    # Stats
    coworker_count = fields.Integer(compute='_compute_coworker_count')
    session_line_count = fields.Integer(compute='_compute_session_line_count')
    session_tokens_last_30d = fields.Integer(
        compute='_compute_session_tokens_30d', string='Tokens (månad)')
    live_external_count = fields.Integer(
        compute='_compute_live_external_count', string='Levande externa',
        help='Antal externa agent-processer som lever just nu (D10-mätpunkt). '
             'Inget tak jämförs mot detta tal — taket är empiriskt.')

    def _compute_session_line_count(self):
        for r in self:
            r.session_line_count = self.env['ai.coworker.session.line'].search_count(
                [('agent_id', '=', r.id)])

    def _compute_session_tokens_30d(self):
        for r in self:
            r.session_tokens_last_30d = self.env['ai.coworker.session.line']._tokens_this_month(
                extra=[('agent_id', '=', r.id)])

    def _compute_coworker_count(self):
        for r in self:
            r.coworker_count = self.env['ai.coworker.agent'].search_count([
                ('agent_id', '=', r.id)
            ])

    def _compute_live_external_count(self):
        """Levande externa processer (D10). Beräknas en gång, samma för alla."""
        count = self.env['ai.coworker.session']._live_external_count()
        for r in self:
            r.live_external_count = count

    def action_open_session_lines(self):
        """Open coworker session lines produced by this agent (stat button)."""
        return {
            'name': 'Agenter', 'type': 'ir.actions.act_window',
            'res_model': 'ai.coworker.session.line', 'view_mode': 'list,form',
            'views': [[False, 'list'], [False, 'form']],
            'domain': [('agent_id', '=', self.id)],
        }

    # ── Extern körning (external-agent-runtime) ─────────────────────

    def _runtime_is_external(self):
        self.ensure_one()
        return self.runtime == 'external'

    def _attach_builtin_tool_by_name(self, tool_name):
        """Koppla ett inbyggt verktyg till agenten via dess NAMN.

        Inbyggda verktyg (core/tools.py) får sina `ai.tool`-poster skapade
        dynamiskt av `_ensure_builtin_tool_records()` och har därför inget
        xmlid — de kan inte refereras med `ref()` i en datafil. Denna metod
        anropas från XML så att seeden förblir deklarativ.

        Idempotent: `(4, id)` lägger bara till länken om den inte finns.
        Returnerar antalet agenter som fick verktyget (för loggning).
        """
        tool = self.env['ai.tool'].search(
            [('name', '=', tool_name)], limit=1)
        if not tool:
            _logger.warning(
                '_attach_builtin_tool_by_name: hittade inget verktyg med '
                'namnet %r — hoppar över. Är _ensure_builtin_tool_records() '
                'körd?', tool_name)
            return 0
        for agent in self:
            agent.write({'tool_ids': [(4, tool.id)]})
        return len(self)

    def _dispatch_external(self, coworker, session, user, prompt=None):
        """Starta denna agent som extern process för en coworker-körning.

        Detta är DEN ENDA dispatch-implementationen (D3/D11): specialistlager
        (t.ex. `saltstack_ai`) ärver den och bygger ingen egen livscykel.

        Användarkontexten (D5) löses upp av anroparen INNAN denna metod —
        nyckeln binds till `user`, och `systemuser` är aldrig tillåtet.

        Args:
            coworker: `ai.coworker`-record som körningen tillhör.
            session: `ai.coworker.session`-record (kan vara tom).
            user: `res.users` — den UPPLÖSTA identiteten (aldrig systemuser).
            prompt: uppdrag att skicka till agenten vid start.

        Returns:
            dict med pid, port, spawn_time, rss_kb — eller None om agenten
            inte är extern.
        """
        self.ensure_one()
        if not self._runtime_is_external():
            return None

        from odoo.addons.ai_agent_core.core import runtime as rt

        if not user:
            raise ValidationError(
                'Extern körning kräver en upplöst användare — '
                'systemuser är aldrig tillåtet (D5).')
        if user.id == self.env.ref('base.user_root').id:
            raise ValidationError(
                'Extern körning får inte köras som systemuser (D5).')

        base_url = self.env['ir.config_parameter'].sudo().get_param(
            'web.base.url', 'http://localhost:8069')

        # Nyckeln skapas som den UPPLÖSTA användaren: `_generate` binder
        # nyckeln till `env.user` (INSERT ... user_id = env.user.id). Det är
        # därför identiteten måste vara löst INNAN vi skapar den (D5).
        # `with_user(user)` sätter env.user; `.sudo()` behövs för att
        # skriva i apikeys-tabellen.
        api_key = self.env['res.users.apikeys'].with_user(user).sudo()._generate(
            scope='rpc',
            name='pi-agent dispatch (coworker %s)' % coworker.id,
            expiration_date=None,
        )

        skills = self.skill_ids.mapped('name') if self.skill_ids else None
        handle = rt.spawn(
            env=self.env,
            coworker_id=coworker.id,
            api_key=api_key,
            base_url=base_url,
            name='pi-agent-coworker-%s' % coworker.id,
            skills=skills,
            prompt=prompt,
        )

        measurement = {
            'pid': handle.pid,
            'port': handle.port,
            'spawn_time': round(handle.duration(), 3),
            'rss_kb': handle.rss_kb(),
        }
        if session:
            session._record_external_run(measurement)
        _logger.info(
            'runtime: dispatchade agent %s för coworker %s som %s '
            '(pid=%s, port=%s, %.3f s)',
            self.id, coworker.id, user.login, handle.pid, handle.port,
            measurement['spawn_time'])
        return measurement

    def _compute_is_buzz_active(self):
        for r in self:
            r.is_buzz_active = bool(self.env['ai.coworker.agent'].search([
                ('agent_id', '=', r.id),
                ('coworker_id.orchestration_mode', '=', 'buzz'),
            ], limit=1))

    def _ensure_partner(self, email_domain='ai.vertel.se'):
        """Create or return res.partner for this agent.

        Called when the agent is added to a Buzz workspace.
        Idempotent — does nothing if partner_id already set.
        """
        self.ensure_one()
        if self.partner_id:
            return self.partner_id
        if not self.name:
            raise ValueError(_('Agent must have a name to create a partner.'))
        alias = (self.alias_name or self.name).lower().replace(' ', '-')
        partner = self.env['res.partner'].sudo().create({
            'name': f'🤖 {self.name}',
            'email': f'agent-{self.id}-{alias}@{email_domain}',
            'is_company': False,
        })
        self.partner_id = partner.id
        return partner

    def _generate_avatar_image(self, avatar_description=''):
        """Generate an avatar image for this agent using an AI image model.

        Falls back to Odoo's default initials avatar if no image model is
        available or the generation fails.
        """
        self.ensure_one()
        if not self.partner_id:
            return False

        prompt = avatar_description or f'Friendly cartoon avatar of {self.name}, professional workplace assistant'
        image_b64 = None

        try:
            model = self.env['ai.model'].sudo().search([
                ('is_text2image', '=', True),
                ('active', '=', True),
            ], limit=1)
            if not model:
                _logger.info('No text2image model available for avatar generation; using fallback')
                return False

            provider = model.provider
            if not provider or not provider.base_url:
                return False

            url = provider.base_url.rstrip('/') + '/images/generations'
            headers = {'Content-Type': 'application/json'}
            if provider.api_key:
                headers['Authorization'] = f'Bearer {provider.api_key}'

            payload = {
                'model': model.name,
                'prompt': prompt,
                'n': 1,
                'size': '256x256',
                'response_format': 'b64_json',
            }
            resp = requests.post(url, headers=headers, json=payload, timeout=60)
            resp.raise_for_status()
            data = resp.json()
            b64 = data['data'][0]['b64_json']
            image_b64 = b64 if b64 else data['data'][0].get('url')
        except Exception as e:
            _logger.warning('AI avatar generation failed for agent %s: %s', self.name, e)
            return False

        if image_b64:
            # Ensure pure base64 string (remove data URL prefix if present)
            if isinstance(image_b64, str) and ',' in image_b64:
                image_b64 = image_b64.split(',', 1)[1]
            self.partner_id.sudo().write({'image_1920': image_b64})
            return True
        return False

    def action_regenerate_avatar(self):
        """Regenerate the agent's avatar from its identity or description."""
        self.ensure_one()
        desc = ''
        if self.identity_id:
            desc = f'Friendly cartoon avatar of {self.name}: {self.identity_id.personality or ""}'
        self._generate_avatar_image(desc)
        return {'type': 'ir.actions.act_window_close'}

    def action_get_quests(self):
        quest_ids = self.env['ai.coworker.agent'].search([
            ('agent_id', '=', self.id)
        ]).mapped("coworker_id").ids
        return {
            'name': 'Quests', 'type': 'ir.actions.act_window',
            'res_model': 'ai.coworker', 'view_mode': 'kanban,list,form',
            'views': [[False, 'kanban'], [False, 'list'], [False, 'form']],
            'target': 'current', 'domain': [('id', 'in', quest_ids)],
        }

    @api.model_create_multi
    def create(self, vals_list):
        """Skapa agenter — applicera default-verktyg (explicit-agent-tools).

        Om en ny agent skapas utan explicita tool_ids, får den
        default_tool_ids från Settings → AI Orkestrering (describe_model,
        odoo_search m.fl.). Befintliga agenter rörs aldrig; agenter med
        egna verktyg respekteras.
        """
        default_names = self._get_default_tool_names()
        if default_names:
            default_tools = self.env['ai.tool'].search(
                [('name', 'in', default_names)])
            if default_tools:
                for vals in vals_list:
                    if 'tool_ids' not in vals:
                        vals['tool_ids'] = [(6, 0, default_tools.ids)]
        return super(AIAgent, self).create(vals_list)

    @api.model
    def _get_default_tool_names(self):
        """Returnera default-verktygsnamnen från Settings (ir.config_parameter).

        Tom/parameter saknas → DEFAULT_AGENT_TOOL_NAMES från res.config.settings.

        avveckla-builtin-fallbacken: ingen hårdkodad sista utväg med INTERNA
        verktyg. Om settings-listan inte kan läsas returneras en TOM lista —
        hellre inga verktyg än odoo-verktyg ingen valt. En varning loggas så
        felet blir synligt i stället för att tyst ge fel verktyg.
        """
        param = self.env['ir.config_parameter'].sudo().get_param(
            'ai_agent_core.default_tool_ids', '')
        names = [n.strip() for n in param.split(',') if n.strip()]
        if names:
            return names
        # Fallback: samma lista som settings-fältets default (säkra verktyg).
        try:
            from .res_config_settings import ResConfigSettings
            return list(ResConfigSettings.DEFAULT_AGENT_TOOL_NAMES)
        except Exception as e:
            _logger.warning(
                'Kunde inte läsa default-verktyg (DEFAULT_AGENT_TOOL_NAMES): '
                '%s — returnerar TOM lista (inga verktyg) i stället för '
                'interna odoo-verktyg', e)
            return []

    def write(self, vals):
        res = super(AIAgent, self).write(vals)
        if 'name' in vals and self.partner_id:
            self.partner_id.sudo().name = f'🤖 {vals["name"]}'
        if 'alias_name' in vals and self.partner_id:
            alias = (vals['alias_name'] or self.name).lower().replace(' ', '-')
            self.partner_id.sudo().email = f'agent-{self.id}-{alias}@ai.vertel.se'
        return res

    def get_agent_name(self):
        """Generate an informative label showing agent's role and capabilities.

        Used by supervisor routing to describe agents to the router LLM,
        and as future mermaid graph labels.

        Returns a multi-line string with role, tools, memories, and model.
        """
        import re
        name = re.sub(r'[()\[\]{}\}:]', ' ', (self.name or '').replace('|', ' ')).strip()
        parts = [f"**{name}**"]

        if self.ai_role:
            parts.append(f"Role: {self.ai_role}")
        if self.ai_goal:
            parts.append(f"Goal: {self.ai_goal[:100]}")

        # Tools
        if self.tool_ids:
            tool_names = [t.name for t in self.tool_ids]
            parts.append(f"Tools: {', '.join(tool_names)}")

        # Memories
        if self.memory_ids:
            mem_names = [m.memory_id.name for m in self.memory_ids if m.memory_id]
            if mem_names:
                parts.append(f"Memories: {', '.join(mem_names)}")

        # Model
        if self.model_id:
            parts.append(f"LLM: {self.model_id.name}")

        return "\n".join(parts)


class AIAgentTool(models.Model):
    _name = 'ai.agent.tool'
    _description = 'Agent Tool'
    _order = 'sequence asc'

    agent_id = fields.Many2one('ai.agent', required=True, ondelete='cascade')
    name = fields.Char('Tool Name', required=True)
    description = fields.Text('Description')
    parameters = fields.Text('Parameters (JSON Schema)')
    risk_level = fields.Selection([
        ('safe', 'Safe'), ('read_only', 'Read Only'),
        ('write', 'Write'), ('destructive', 'Destructive'),
    ], default='read_only')
    sequence = fields.Integer(default=10)


class AIAgentMemory(models.Model):
    _name = 'ai.agent.memory'
    _description = 'Agent Memory Link'
    _order = 'sequence asc'

    agent_id = fields.Many2one('ai.agent', required=True, ondelete='cascade',
                                string='Agent')
    memory_id = fields.Many2one('ai.memory', required=True, ondelete='cascade',
                                 string='Memory')
    sequence = fields.Integer(default=10)


    # ══════════════════════════════════════════════════════════════════
    # Default-skills för Allmän kärna
    # ══════════════════════════════════════════════════════════════════

    @api.model
    def _ensure_default_skills(self):
        """Koppla default-skillarna till Allmän kärna (idempotent).

        Anropas som `<function>` i `data/vertel_skills.xml`, UTANFÖR
        noupdate-blocket.

        VARFÖR EN FUNKTION OCH INTE EN `<record>`: xmlid:n
        `agent_default_core` skapas i `default_coworker.xml` med
        `<data noupdate="1">` → `noupdate=true` i ir_model_data. Odoos
        `_load_records` hoppar då över ALL vidare bearbetning av den
        xmlid:n — även från en annan fil. En `<record>` på samma xmlid i
        `vertel_skills.xml` hade alltså ingen verkan, och felet var tyst:
        skillsen skapades, men kopplingen uteblev.

        Mätt på social 2026-09-22: `skill_okf_summarize` fanns som rad men
        `ai_agent_skill_rel` saknade kopplingen till agent 1.

        Idempotent: lägger bara till länkar som saknas, rör inga befintliga.
        """
        agent = self.env.ref('ai_agent_core.agent_default_core',
                             raise_if_not_found=False)
        if not agent:
            _logger.warning(
                'ai.agent: agent_default_core saknas — kan inte koppla '
                'default-skills')
            return True

        wanted = [
            'skill_vertel_skills',
            'skill_vertel_infra',
            'skill_vertel_openspec',
            'skill_vertel_clicktest',
            'skill_okf_summarize',
        ]
        added = []
        for xmlid in wanted:
            skill = self.env.ref('ai_agent_core.%s' % xmlid,
                                 raise_if_not_found=False)
            if not skill:
                _logger.warning('ai.agent: skillen %s saknas', xmlid)
                continue
            if skill not in agent.skill_ids:
                agent.write({'skill_ids': [(4, skill.id)]})
                added.append(xmlid)
        if added:
            _logger.info('ai.agent: kopplade %s till %s',
                         ', '.join(added), agent.name)
        return True
