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

    # Index som Odoo inte kan deklarera på ett fält (sammansatta eller
    # uttrycksbaserade) skapas här (coworker-form-performance D5). Samma väg som
    # ai.okf.concept._auto_init använder för sina SQL-funktioner: `checkmodule
    # --init` kör INTE migrationsfiler, så en färsk databas får annars indexen
    # först vid nästa `--update`. Migration 18.0.1.335 lägger dem på befintliga
    # databaser; båda vägarna är idempotenta (IF NOT EXISTS).
    _PERF_INDEXES = (
        # Månads-/dags-/veckosumman filtrerar på create_date och grupperar på
        # coworker_id. Utan dessa väljer Postgres två-värdes-indexet på
        # `informative` och läser alla rader (mätt: 186 ms, 15 912 bortfiltrerade).
        ('ai_coworker_session_line_coworker_create_idx',
         '(coworker_id, create_date)'),
        ('ai_coworker_session_line_create_date_idx',
         '(create_date)'),
        # Dedup-uppslagningen (pi-message-persist-dedup): session + hash.
        ('ai_coworker_session_line_session_hash_idx',
         '(session_id, content_hash)'),
    )

    def _auto_init(self):
        res = super()._auto_init()
        self._ensure_perf_indexes()
        return res

    @api.model
    def _ensure_perf_indexes(self):
        """Skapa de index som prestandafrågorna kräver (idempotent)."""
        cr = self.env.cr
        for name, columns in self._PERF_INDEXES:
            cr.execute(
                'CREATE INDEX IF NOT EXISTS %s ON %s %s' % (
                    name, self._table, columns))
    # B2: sekvensen är ORDNING, inte identitet — och ordningen måste vara
    # entydig. `_next_session_sequence` läser max och skriver max+1 utan lås,
    # så två samtidiga skrivningar i samma session kunde få samma sekvens
    # (dubblettraden i session 18204). Utan constraint kan historiken inte
    # sorteras deterministiskt, och `_build_history_from_lines` kan para fel
    # assistant-rad med fel verktygsspår.
    #
    # Migrationen i migrations/18.0.1.334 städar befintliga dubbletter FÖRE
    # denna constraint läggs på — annars faller ALTER TABLE och hela
    # uppgraderingen rullas tillbaka.
    _sql_constraints = [
        ('session_sequence_uniq',
         'UNIQUE(session_id, sequence)',
         'The sequence must be unique within a session.'),
    ]

    session_id = fields.Many2one(
        'ai.coworker.session', required=True, ondelete='cascade',
        string='Thread', index=True,
    )
    # Coworker lagras på raden (coworker-form-performance D2) — samma mönster
    # som ai.coworker.error använder sedan tidigare. Utan den här kolumnen
    # måste varje budgetfråga joina sessionen, och depends-kedjan blir två led
    # (`session_ids.session_line_ids`) vilket tvingar Odoo att materialisera
    # varje rad som ORM-objekt innan compute ens börjar.
    coworker_id = fields.Many2one(
        'ai.coworker', string='AI Coworker',
        related='session_id.coworker_id', store=True, index=True)
    # Vilken agent/roll/skill/tool som producerade denna rad — för spårning
    # per agent & modell samt smartknappar från ai.agent/ai.tool/ai.skill.
    agent_id = fields.Many2one('ai.agent', string='Agent', index=True,
        help='The agent that produced this message line. Filled on '
             'multi-agent delegation (path A) and on single-assistant lines '
             'where the agent is known.')
    skill_id = fields.Many2one('ai.skill', string='Skill', index=True,
        help='The skill that was built or tested on this message line, where '
             'that is known (e.g. from the agent skill or a skill session '
             'context).')
    tool_id = fields.Many2one('ai.tool', string='Tool', index=True,
        help='The tool that was called (on tool lines). Filled from tool_name '
             'against ai.tool so that ai.tool can list its messages.')
    sequence = fields.Integer('Order', default=10)
    role = fields.Selection([
        ('user', 'User'),
        ('assistant', 'Assistant'),
        ('tool', 'Tool'),
        ('system', 'System'),
    ], required=True, default='user')
    # Innehålls-hash för dedup (pi-message-persist-dedup D1). Pi skickar hela
    # sin messages[] vid varje anrop; samma post får bara ge en rad. Hashen
    # gör uppslagningen till en punktfråga i stället för en genomgång av
    # sessionens alla rader (mätt: 34 866 rader i en session).
    content_hash = fields.Char(
        'Content Hash', size=32, index=True, readonly=True,
        help='md5 of role|tool_call_id|content. Used to dedupe Pi posts — the '
             'same post must yield only one line per session.')
    content = fields.Text('Message Content')
    reasoning = fields.Text('Reasoning',
        help='The model reasoning text (reasoning_content/reasoning) produced '
             'before this message; kept for review without being shown in '
             'the answer.')
    debug_info = fields.Text('Debug/Reasoning',
        help='The agent reasoning/narration (not shown in the answer to the '
             'user, but stored here for review).')
    source_urls = fields.Text('Source URLs',
        help='URLs the agent used (one per line).')
    tool_calls = fields.Text('Tool Calls (JSON)')
    tool_name = fields.Char('Tool Name')
    tool_call_id = fields.Char('Tool Call ID', index=True,
        help="OpenAI's tool_call_id for tool lines. Paired with the assistant "
             "line's tool_calls[].id so the history can be sent back to the "
             'provider in a valid format. Without it, strict providers (e.g. '
             'deepseek) reject the whole call with 422 "missing field '
             'tool_call_id".')
    token_input = fields.Integer('Input Tokens', default=0)
    token_output = fields.Integer('Output Tokens', default=0)
    # ── Per-varv-räkning (webui-show-work-again 4.1) ────────────────────
    # En tur som gör flera verktygsrundor skickar HELA historiken igen vid
    # varje varv, så `token_input` är en summa över varv — inte en enda
    # prompt. Åtta rundor à ~13k ger ~104k, vilket ser orimligt ut som ett
    # ensamt tal (mätt: session 27953, `token_input = 104 437`).
    #
    # Siffran är korrekt som debiteringsunderlag och rörs inte. Dessa två
    # fält bär UPPDELNINGEN, så talet går att förstå: hur många varv turen
    # gjorde och vad varje varv kostade i input.
    rounds = fields.Integer('Rounds', default=0,
        help='Number of LLM rounds in the turn. 1 = answered directly without '
             'tools. Each round resends the whole history, so token_input is '
             'the sum over those rounds.')
    round_inputs = fields.Text('Input per varv',
        help='Comma-separated input tokens per round, in order. The sum equals '
             'token_input. Empty for lines written before 2026-11-06.')

    # Systemtoken tracking
    model_real = fields.Char('Model Used',
        help='The actual model name returned by the provider (e.g. claude-sonnet-4-20250514)')
    sys_multiplier = fields.Float('System Token Multiplier', default=1.0,
        help='Multiplier from ai.model at the time this line was created')
    token_sys = fields.Integer('Systemtokens', compute='_compute_token_sys', store=True,
        help='BILLING BASIS: (token_input + token_output) x sys_multiplier. '
             'Includes the Vertel margin — this is what the customer is '
             'charged, NOT a cost. The actual cost in USD sits on the line '
             'cost_usd. The two are never merged.')

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
        help='Why this line turn/step ended. Same vocabulary as the session '
             'finish_reason.')
    interrupted = fields.Boolean('Interrupted', default=False,
        help='True when the turn was interrupted mid-stream and the delivered '
             'prefix was preserved.')

    # ── Kostnad och osäkerhet (utfall-och-tokenmatning) ────────────────
    # Ögonblicksbilder från ai.model vid radens skapande — samma mönster
    # som sys_multiplier. `token_sys` är DEBITERINGSGRUND (inkl. marginal);
    # `cost_usd` är FAKTISK kostnad. De slås aldrig ihop.
    cost_input_1k = fields.Float('Input Cost per 1K', digits=(12, 8),
        help='Modellens input-pris per 1K tokens vid radens skapande.')
    cost_output_1k = fields.Float('Output Cost per 1K', digits=(12, 8),
        help='Modellens output-pris per 1K tokens vid radens skapande.')
    cached_tokens = fields.Integer('Cached Tokens', default=0,
        help='Number of input tokens that were cache hits (cheaper).')
    usage_reported = fields.Boolean('Usage Reported', default=True,
        help='False when the provider did not report token usage. Separates '
             '"no tokens" from "we do not know" — an unmeasured line must not '
             'look free.')
    cost_usd = fields.Float('Cost (USD)', compute='_compute_cost_usd',
        store=True, digits=(16, 8),
        help='Actual cost in USD: (in - cached)/1000 x cost_input_1k '
             '+ out/1000 x cost_output_1k. Unmeasured (0) when '
             'usage_reported=False.')

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
    # NOT NULL med default false (coworker-form-performance D4): flaggan bär
    # ingen tredje betydelse. Historiskt fanns NULL-rader som Python räknade
    # som false men SQL exkluderade — två semantiker för samma fråga.
    # Migration 18.0.1.335 backfillar dem; create()/write() normaliserar
    # None → False så inget nytt NULL kan uppstå.
    informative = fields.Boolean('Informative', default=False, index=True,
        help='True = the line is traceability but is NOT counted in the budget '
             'or burn rate.')

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

    # PostgreSQL kan inte lagra NUL (0x00) i text — hela INSERT:en faller på
    # "A string literal cannot contain NUL (0x00) characters".
    # _persist_pi_messages sväljer felet med en varning, så symptomet blev
    # tyst: sessionshistoriken tappades (ledningssystem 2026-10-07, 180 ggr).
    # Verktygsutdata (binära filhuvuden, terminalfångst) kan innehålla NUL.
    # Sanera därför ALLA strängvärden, inte bara `content` — annars återkommer
    # felet på nästa textfält.
    @api.model
    def _strip_nul(self, vals):
        cleaned = None
        for fname, value in vals.items():
            if isinstance(value, str) and '\x00' in value:
                if cleaned is None:
                    cleaned = dict(vals)
                cleaned[fname] = value.replace('\x00', '')
        return cleaned if cleaned is not None else vals

    @api.model
    def _normalize_informative(self, vals):
        """`informative=None` lagras som False (kolumnen är NOT NULL)."""
        if vals.get('informative', False) is None:
            vals = dict(vals, informative=False)
        return vals

    @api.model
    def _content_hash(self, vals):
        """md5 av role|tool_call_id|content — dedup-nyckeln (D1)."""
        import hashlib
        raw = '|'.join([
            str(vals.get('role') or ''),
            str(vals.get('tool_call_id') or ''),
            str(vals.get('content') or ''),
        ])
        return hashlib.md5(raw.encode('utf-8')).hexdigest()

    @api.model
    def _with_content_hash(self, vals):
        """Sätt content_hash om det inte redan är satt."""
        if vals.get('content_hash'):
            return vals
        return dict(vals, content_hash=self._content_hash(vals))

    @api.model_create_multi
    def create(self, vals_list):
        return super().create([
            self._with_content_hash(
                self._strip_nul(self._normalize_informative(v)))
            for v in vals_list])

    def write(self, vals):
        """Tillåt endast metadatafält — innehållet är append-only."""
        blocked = set(vals) & self._IMMUTABLE_FIELDS
        if blocked:
            from odoo.exceptions import UserError
            raise UserError(
                'Session lines are append-only; cannot modify %s. '
                'Create a new line instead.' % sorted(blocked))
        return super().write(
            self._strip_nul(self._normalize_informative(vals)))

    def _enrich_from_client(self, vals):
        """Berika en server-skriven rad med klientens metadata.

        Enda legitima vägen runt append-only-skyddet (webui-show-work-again
        5.3): servern skriver turens rad först (content, reasoning, tokens),
        och klientens POST får fylla på de fält servern inte hade —
        `debug_info`, `source_urls`, `tool_calls`.

        Skyddet finns för att INNEHÅLLET inte får skrivas om: `content`,
        `role`, tokens. Den här metoden rör bara metadata, och bara fält som
        är tomma på raden — den kan därför aldrig skriva över något servern
        redan satt, och aldrig nolla något.

        Anropas av `ai.coworker.session._save_client_response`. Använd den
        inte för att ändra innehåll; då är `create()` rätt väg.
        """
        allowed = {'debug_info', 'source_urls', 'tool_calls'}
        safe = {}
        for fname, value in (vals or {}).items():
            if fname not in allowed:
                continue
            if not value:
                continue
            if (getattr(self, fname) or '').strip():
                continue  # serverns värde vinner — aldrig överskrivning
            safe[fname] = value
        if not safe:
            return False
        return super(AICoworkerSessionLine, self).write(
            self._strip_nul(safe))

    def unlink(self):
        """Sessionsrader får inte raderas (append-only, granskningsbart)."""
        from odoo.exceptions import UserError
        raise UserError(
            'Session lines are append-only and cannot be deleted.')

    # ── Token-totaler (coworker-form-performance D1) ─────────────────────
    # EN grupperad fråga för alla perioder som formuläret frågar efter.
    # Tidigare hade fyra computes var sin Python-loop över alla rader, vilket
    # gjorde formulärladdningen 27 s för en medarbetare med 225 774 rader.

    @api.model
    def _token_totals(self, coworker_ids, month_start, day_start, week_start):
        """Σ token_sys per medarbetare och period, ur en `_read_group`.

        Returnerar ``{coworker_id: {'month': int, 'day': int,
        'week_by_day': {date: int}}}``.

        * ``month``      — summan från ``month_start`` (kalendermånad)
        * ``day``        — summan för ``day_start`` (kalenderdag)
        * ``week_by_day`` — per dag från ``week_start`` (burn rate)

        Endast icke-informativa rader räknas (utfall-och-tokenmatning).
        ``informative`` är NOT NULL sedan 18.0.1.335, så ``= false`` täcker
        även de historiska rader som tidigare var NULL — samma summa som den
        gamla Python-loopen gav.

        Datumgränserna jämförs på Python-datum. Odoos ``create_date:day``
        grupperar i användarens tidszon, medan den gamla loopen läste
        ``create_date.date()`` (UTC). Samma dag som nuvarande kod räknar.
        """
        ids = [int(c) for c in (coworker_ids or []) if c]
        totals = {
            cid: {'month': 0, 'day': 0, 'week_by_day': {}}
            for cid in ids
        }
        if not ids:
            return totals
        groups = self._read_group(
            [('coworker_id', 'in', ids),
             ('informative', '=', False),
             ('create_date', '>=', week_start)],
            ['coworker_id', 'create_date:day'],
            ['token_sys:sum'],
        )
        for coworker, day, token_sys in groups:
            if not coworker or not day:
                continue
            # `create_date:day` ger en datetime (midnatt) — jämför på datum,
            # samma som den gamla loopen gjorde med `create_date.date()`.
            day = day.date() if hasattr(day, 'date') else day
            bucket = totals.setdefault(
                coworker.id,
                {'month': 0, 'day': 0, 'week_by_day': {}})
            amount = int(token_sys or 0)
            bucket['week_by_day'][day] = (
                bucket['week_by_day'].get(day, 0) + amount)
            if day >= month_start:
                bucket['month'] += amount
            if day >= day_start:
                bucket['day'] += amount
        return totals

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
