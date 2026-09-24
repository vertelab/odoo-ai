# -*- coding: utf-8 -*-
"""
ai.coworker.session.line — Individual messages within a thread.

Each line represents one message in a conversation thread.
"""
from odoo import models, fields, api
from datetime import timedelta


class AICoworkerSessionLine(models.Model):
    _name = 'ai.coworker.session.line'
    _description = 'Session Message Line'
    _order = 'session_id, sequence asc'

    session_id = fields.Many2one(
        'ai.coworker.session', required=True, ondelete='cascade',
        string='Thread',
    )
    # Vilken agent/roll/skill/tool som producerade denna rad — för spårning
    # per agent & modell samt smartknappar från ai.agent/ai.tool/ai.skill.
    agent_id = fields.Many2one('ai.agent', string='Agent', index=True,
        help='Den agent som producerade denna meddelanderad. Fylls vid '
             'multi-agent-delegering (Väg A) och enkel-assistentrader där '
             'agenten är känd.')
    skill_id = fields.Many2one('ai.skill', string='Skill', index=True,
        help='Den skill som byggdes/testades i denna meddelanderad (där det '
             'är känt, t.ex. från agentens skill eller en skill-sessionskontext).')
    tool_id = fields.Many2one('ai.tool', string='Tool', index=True,
        help='Verktyget som anropades (för tool-rader). Fylls från '
             'tool_name mot ai.tool så att ai.tool kan lista sina meddelanden.')
    sequence = fields.Integer('Order', default=10)
    role = fields.Selection([
        ('user', 'User'),
        ('assistant', 'Assistant'),
        ('tool', 'Tool'),
        ('system', 'System'),
    ], required=True, default='user')
    content = fields.Text('Message Content')
    reasoning = fields.Text('Reasoning',
        help='Modellens "gråa" tänketext (reasoning_content/reasoning) som '
             'produsierades före detta meddelande; för granskning utan att det '
             'visas i svaret.')
    debug_info = fields.Text('Debug/Resonemang',
        help='Agentens resonemang/narrering (visas inte i svaret till '
             'användaren, men sparas här för granskning).')
    source_urls = fields.Text('Käll-URL:er',
        help='URL:er som agenten använde (en per rad).')
    tool_calls = fields.Text('Tool Calls (JSON)')
    tool_name = fields.Char('Tool Name')
    token_input = fields.Integer('Input Tokens', default=0)
    token_output = fields.Integer('Output Tokens', default=0)

    # Systemtoken tracking
    model_real = fields.Char('Model Used',
        help='The actual model name returned by the provider (e.g. claude-sonnet-4-20250514)')
    sys_multiplier = fields.Float('Systemtoken-multiplikator', default=1.0,
        help='Multiplier from ai.model at the time this line was created')
    token_sys = fields.Integer('Systemtokens', compute='_compute_token_sys', store=True,
        help='(token_input + token_output) × sys_multiplier')

    # ── Utfall per rad (utfall-och-tokenmatning) ────────────────────────
    # Radens eget utfall. `TokenEvent`/`ChatResponse` bär redan
    # `finish_reason` — det nådde tidigare aldrig raden. `interrupted`
    # markerar en tur som avbröts mitt i strömmen (prefixet bevaras).
    finish_reason = fields.Selection([
        ('stop', 'Stop'),
        ('length', 'Length'),
        ('tool_calls', 'Tool Calls'),
        ('content_filter', 'Content Filter'),
        ('refusal', 'Refusal'),
        ('max_rounds', 'Max Rounds'),
        ('max_tokens', 'Max Tokens'),
        ('timeout', 'Timeout'),
        ('cancelled', 'Cancelled'),
        ('error', 'Error'),
        ('idle', 'Idle'),
        ('closed', 'Closed'),
        ('interrupted', 'Interrupted'),
        ('new_session', 'New Session'),
    ], string='Finish Reason',
        help='Varför denna rads tur/steg slutade. Samma vokabulär som '
             'sessionens finish_reason.')
    interrupted = fields.Boolean('Interrupted', default=False,
        help='True när turen avbröts mitt i en ström och det levererade '
             'prefixet bevarades.')

    # ── Kostnad och osäkerhet (utfall-och-tokenmatning) ────────────────
    # Ögonblicksbilder från ai.model vid radens skapande — samma mönster
    # som sys_multiplier. `token_sys` är DEBITERINGSGRUND (inkl. marginal);
    # `cost_usd` är FAKTISK kostnad. De slås aldrig ihop.
    cost_input_1k = fields.Float('Input Cost per 1K', digits=(12, 8),
        help='Modellens input-pris per 1K tokens vid radens skapande.')
    cost_output_1k = fields.Float('Output Cost per 1K', digits=(12, 8),
        help='Modellens output-pris per 1K tokens vid radens skapande.')
    cached_tokens = fields.Integer('Cached Tokens', default=0,
        help='Antal input-tokens som var cache-träffar (billigare).')
    usage_reported = fields.Boolean('Usage Reported', default=True,
        help='False när providern inte rapporterade token-usage. Skiljer '
             '"inga tokens" från "vi vet inte" — en omätt rad får inte se '
             'gratis ut.')
    cost_usd = fields.Float('Cost (USD)', compute='_compute_cost_usd',
        store=True, digits=(16, 8),
        help='Faktisk kostnad i USD: (in − cached)/1000 × cost_input_1k '
             '+ ut/1000 × cost_output_1k. Omätt (0) när usage_reported=False.')

    @api.depends('token_input', 'token_output', 'cached_tokens',
                 'cost_input_1k', 'cost_output_1k', 'usage_reported')
    def _compute_cost_usd(self):
        for line in self:
            if not line.usage_reported:
                # Omätt är inte 0 — lämna utan värde så aggregat kan skilja dem.
                line.cost_usd = 0.0
                continue
            billed_input = max((line.token_input or 0) - (line.cached_tokens or 0), 0)
            line.cost_usd = (
                billed_input / 1000.0 * (line.cost_input_1k or 0.0)
                + (line.token_output or 0) / 1000.0 * (line.cost_output_1k or 0.0)
            )

    # ── Informativa rader (utfall-och-tokenmatning) ────────────────────
    # Rader som bär spårbarhet men INTE ska räknas i budgeten (t.ex.
    # per-agent-tokens från supervisor/konsensus, där den aggregerade
    # totalen redan bokförts på sessionen). Utan denna flagga dubbelräknar
    # _compute_session_line_count dessa rader.
    informative = fields.Boolean('Informative', default=False, index=True,
        help='True = raden är spårbarhet men räknas INTE i budget/burn rate.')

    @api.depends('token_input', 'token_output', 'sys_multiplier')
    def _compute_token_sys(self):
        for line in self:
            line.token_sys = int((line.token_input + line.token_output) * line.sys_multiplier)

    # ── Append-only (improve-ai-coworker-memory-and-tools 1.5) ──────────
    # Sessionsrader är granskningsbara och får inte skrivas över eller
    # raderas under sessionens livstid. Endast rena lifecycle-/metadatafält
    # får uppdateras (t.ex. agent_id/tool_id som kan fyllas i efteråt vid
    # specialist-delegation). Innehåll och roll är immutabla.
    #
    # Utfalls-/kostnadsfälten (finish_reason, interrupted, cost_*, cached_tokens,
    # usage_reported, informative) är metadata och får uppdateras — de tillhör
    # livscykeln, inte innehållet.
    _IMMUTABLE_FIELDS = frozenset({
        'session_id', 'role', 'content', 'tool_calls', 'tool_name',
        'sequence', 'token_input', 'token_output', 'model_real',
        'sys_multiplier', 'reasoning', 'debug_info', 'source_urls',
    })

    def write(self, vals):
        """Tillåt endast metadatafält — innehållet är append-only."""
        blocked = set(vals) & self._IMMUTABLE_FIELDS
        if blocked:
            from odoo.exceptions import UserError
            raise UserError(
                'Session lines are append-only; cannot modify %s. '
                'Create a new line instead.' % sorted(blocked))
        return super().write(vals)

    def unlink(self):
        """Sessionsrader får inte raderas (append-only, granskningsbart)."""
        from odoo.exceptions import UserError
        raise UserError(
            'Session lines are append-only and cannot be deleted.')

    @api.model
    def _tokens_since(self, days=30, extra=(), create_field='create_date'):
        """Summa token_input+token_output för rader yngre än `days`, som valfritt
        matchar `extra`-domänvillkor. Används av smartknappar på ai.model/
        ai.agent/ai.tool/ai.skill för att visa senaste månadens tokenförbrukning.
        """
        since = fields.Datetime.now() - timedelta(days=max(days, 1))
        domain = [(create_field, '>=', since)] + list(extra)
        rows = self.search(domain)
        return sum((r.token_input or 0) + (r.token_output or 0) for r in rows)

    @api.model
    def _tokens_this_month(self, extra=(), create_field='create_date'):
        """Summa token_input+token_output för innevarande KALENDERmånad (1:a → nu),
        som valfritt matchar `extra`. Matchar budget-semantiken (monthly_cap).
        """
        from datetime import datetime as _dt
        now = _dt.now()
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        domain = [(create_field, '>=', month_start)] + list(extra)
        rows = self.search(domain)
        return sum((r.token_input or 0) + (r.token_output or 0) for r in rows)
