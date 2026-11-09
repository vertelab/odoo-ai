# -*- coding: utf-8 -*-
"""ai.coworker.session — standalone session model for agent runs."""

import json, logging, re, uuid
from datetime import date, timedelta
from odoo import models, fields, api, SUPERUSER_ID, _
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)

# Resolver-registry för kostnadskontext (D9): {nyckel → callable(session)}.
# Callable tar en ai.coworker.session och härleder partner_id (och ev.
# domänfält). Bryggor registrerar strategier vid modulimport — core
# förblir domän-rent (inga modellreferenser här).
_COST_CONTEXT_STRATEGIES = {}


def register_cost_context_strategy(key, fn):
    """Registrera en resolver-strategi (nyckel → callable(session))."""
    _COST_CONTEXT_STRATEGIES[key] = fn


def _handover_kv_key(value):
    """KV-nyckel för en session — NATS-nycklar tål inte `:` eller `/`.

    Samma kodning som `lockKey()` i `nats.ts` (base64url): Pi-extensionen
    och Odoo måste räkna fram EXAKT samma nyckel, annars låser de olika
    nycklar och två ägare kan släppas in.
    """
    import base64
    raw = str(value or '').encode('utf-8')
    return base64.urlsafe_b64encode(raw).decode('ascii').rstrip('=')


def get_cost_context_strategy(key):
    """Hämta en registrerad resolver-strategi (eller None)."""
    return _COST_CONTEXT_STRATEGIES.get(key)


class AICoworkerSession(models.Model):
    _name = 'ai.coworker.session'
    _inherit = ['mail.thread']
    _description = 'AI Session'
    _order = 'create_date desc'

    # Index som inte hör till ett enskilt fält (coworker-form-performance D5).
    # Skapas vid modulinitiering så en färsk databas får dem utan migration —
    # `checkmodule --init` kör inte migrationsfiler. Migration 18.0.1.335 gör
    # samma sak på befintliga databaser.
    _PERF_INDEXES = (
        # Sessionsräknaren (månad) och sessionens gruppering per användare.
        ('ai_coworker_session_coworker_create_idx',
         '(coworker_id, create_date)'),
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

    ai_task_id = fields.Many2one('ai.org.task', string='AI Task',
        help='The AI org task (ai.org.task) this session works on. Created '
             'automatically on checkout. NOTE: distinct from task_id '
             '(project.task), which the project_ai bridge adds.')

    name = fields.Char(default=lambda self: str(uuid.uuid4())[:8])

    # Stabil sessionsidentitet för gateway-korrelation
    # (gateway-anropseffektivitet D1). `name` är en VISNINGSETIKETT som
    # sätts från prompt/subject och kan klippas olika på olika ställen
    # (ai_session rad 1051: prompt[:80], ai_coworker: name[:64] som
    # x-bf-session-id) — två trunkeringar av samma sträng blir aldrig
    # lika, så korrelationen mot `bifrost.request.log.session_id` gav
    # alltid noll träffar. Detta fält sätts EN gång vid skapande och
    # skrivs aldrig över av visningsnamnet.
    gateway_session_id = fields.Char(
        'Gateway Session ID', index=True, copy=False,
        default=lambda self: str(uuid.uuid4()),
        help='Stable id sent as x-bf-session-id to the gateway and matched '
             'against the gateway log. Distinct from `name` (display label) — '
             'set once, never overwritten.')

    coworker_id = fields.Many2one('ai.coworker', string='Coworker', ondelete='cascade',
        index=True)
    skill_id = fields.Many2one('ai.skill', string='Skill',
        help='Skill being built/improved in this session')
    agent_id = fields.Many2one('ai.agent', string='Agent')
    identity_id = fields.Many2one('ai.identity', string='Identity')

    status = fields.Selection([
        ('draft', 'Draft'), ('active', 'Active'),
        ('done', 'Done'), ('error', 'Error'),
    ], default='draft')

    # Leverans-/lärandemetadata (inittering, användarbedömning, feldetalj).
    # init_type = hur sessionen startades (kanal/källa). Används för att
    # analysera varifrån AI-trafiken kommer samt för lessons learned.
    init_type = fields.Selection([
        ('web_ui', 'Web Chat UI'),
        ('chat', 'Discuss — Private Chat'),
        ('channel', 'Discuss — Team Channel'),
        ('mail', 'Incoming Mail'),
        ('cron', 'Scheduled Action'),
        ('server_action', 'Server Action'),
        ('powerbox', 'Powerbox'),
        ('manual', 'Manual'),
        ('webhook', 'Webhook'),
        ('openai_api', 'Externt API (/ai/v1)'),
        ('watch', 'Watch — Data Change'),
        ('heartbeat', 'Heartbeat — Proactive'),
    ], string='Start via', index=True, readonly=True, tracking=True,
        help='How the session was started (channel/source). Filled when the '
             'session is created, where the source is known.')

    # ── Livscykel (session-observability) ───────────────────────────────
    # `init_type` säger VILKEN yta som startade sessionen (kanal/källa).
    # `start_reason` säger VARFÖR just den här sessionen skapades — Pi:s
    # `session_start` bär `startup|new|resume|fork`, och det är skillnaden
    # mellan en färsk konversation och en återupptagen som gör statistiken
    # meningsfull. Fälten kompletterar varandra; ingen ersätter den andra.
    start_reason = fields.Selection([
        ('startup', 'Startup'),
        ('new', 'New Session'),
        ('resume', 'Resume'),
        ('fork', 'Fork'),
        ('api', 'API'),
        ('cron', 'Scheduled'),
        ('mail', 'Incoming Mail'),
        ('webhook', 'Webhook'),
        ('heartbeat', 'Heartbeat'),
        ('manual', 'Manual'),
    ], string='Start Reason', index=True, readonly=True,
        help='Why this session was created. Distinct from init_type, which '
             'says which surface started it.')

    close_event_ids = fields.One2many(
        'ai.coworker.session.close_event', 'session_id',
        string='Close Events',
        help='Every closure of this session, oldest first. A session may be '
             'closed and reopened many times; each closure is its own row.')
    close_event_count = fields.Integer(
        'Closures', compute='_compute_close_event_count', store=False)
    last_close_reason = fields.Selection(
        related='close_event_ids.reason', string='Last Close Reason',
        readonly=True,
        help='Why the session was closed most recently. The full history is '
             'in the close events.')
    last_close_at = fields.Datetime(
        related='close_event_ids.at', string='Last Closed',
        readonly=True)

    # ── Spår-översikt (session-observability) ────────────────────────────
    # Räknare för sessionens formulär. De läser spår-tabellerna, inte
    # `line`, och påverkar därför ingen budget.
    agent_run_ids = fields.One2many(
        'ai.coworker.session.agent_run', 'session_id',
        string='Agent Runs', readonly=True)
    tool_call_ids = fields.One2many(
        'ai.coworker.session.tool_call', 'session_id',
        string='Tool Calls', readonly=True)
    agent_run_count = fields.Integer(
        'Agent Runs', compute='_compute_trace_counts', store=False)
    tool_call_count = fields.Integer(
        'Tool Calls', compute='_compute_trace_counts', store=False)
    tool_error_count = fields.Integer(
        'Tool Errors', compute='_compute_trace_counts', store=False)
    trace_agent_count = fields.Integer(
        'Agents', compute='_compute_trace_counts', store=False)

    @api.depends('close_event_ids', 'close_event_ids.at')
    def _compute_close_event_count(self):
        for rec in self:
            rec.close_event_count = len(rec.close_event_ids)

    @api.depends('agent_run_ids', 'tool_call_ids', 'tool_call_ids.is_error')
    def _compute_trace_counts(self):
        for rec in self:
            rec.agent_run_count = len(rec.agent_run_ids)
            rec.tool_call_count = len(rec.tool_call_ids)
            rec.tool_error_count = len(
                rec.tool_call_ids.filtered('is_error'))
            rec.trace_agent_count = len(
                rec.agent_run_ids.mapped('agent_id'))
    user_rating = fields.Selection([
        ('down', 'Unsatisfied'),
        ('neutral', 'Neutral'),
        ('up', 'Satisfied'),
        ('unassessed', 'Not assessed'),
    ], string='User Rating', default='unassessed',
        help='The user subjective assessment of the answer (feedback for '
             'improving the coworker over time).')
    error_detail = fields.Text('Error Description', readonly=True,
        help='Filled when the session ends with an error-ish status (the '
             'exception message, for troubleshooting and analysis).')

    # ── Extern körning (external-agent-runtime D6/D10) ───────────────
    # Mätpunkten: varje extern körning loggar PID, minne, starttid och
    # varaktighet. Tröskeln för samtidighet är EMPIRISK och sätts inte nu —
    # men ska kunna avläsas ur dessa fält när frågan uppstår.
    external_pid = fields.Integer('External PID', readonly=True,
        help='PID of the external agent process (when runtime=external).')
    external_port = fields.Integer('External Port', readonly=True)
    external_started_at = fields.Datetime('External Start Time', readonly=True)
    external_rss_kb = fields.Integer('External RSS (kB)', readonly=True,
        help='The process RSS at dispatch — the measurement point for the '
             'memory profile.')
    external_spawn_time = fields.Float('Spawn Time (s)', readonly=True,
        help='Cold start time in seconds (D9: should be under ~1 s).')
    external_duration = fields.Float('Duration (s)', readonly=True,
        help='The duration of the run, in seconds.')

    @api.model_create_multi
    def create(self, vals_list):
        """Säkra gateway_session_id vid skapande (D1).

        Fältet har en default, men en explicit `create`-override garanterar
        att äldre anropsställen som skickar egna vals (t.ex. message_new via
        mailgateway) aldrig lämnar det tomt — en session utan
        gateway-identitet kan inte korreleras mot gateway-loggen.
        """
        for vals in vals_list:
            if not vals.get('gateway_session_id'):
                vals['gateway_session_id'] = str(uuid.uuid4())
        return super().create(vals_list)

    def _record_external_run(self, measurement):
        """Skriv mätpunkten för en extern körning på sessionsraden (D10).

        Anropas av `ai.agent._dispatch_external` direkt efter spawn. Vi
        skriver bara det vi faktiskt mäter — inga påhittade fält.
        """
        self.ensure_one()
        vals = {
            'external_pid': measurement.get('pid'),
            'external_port': measurement.get('port'),
            'external_started_at': fields.Datetime.now(),
            'external_rss_kb': measurement.get('rss_kb'),
            'external_spawn_time': measurement.get('spawn_time'),
        }
        self.sudo().write(vals)
        return vals

    # ── Livscykel för externa processer (D6/D10) ────────────────────

    def _external_alive(self):
        """Lever den externa processen? PID + (om möjligt) /status."""
        self.ensure_one()
        if not self.external_pid:
            return False
        from odoo.addons.ai_agent_core.core import runtime as rt
        return rt.pid_alive(self.external_pid)

    def action_abort_external(self):
        """Avbryt en extern körning: SIGTERM → respit → SIGKILL (D6)."""
        self.ensure_one()
        from odoo.addons.ai_agent_core.core import runtime as rt
        if not self.external_pid:
            return False
        gone = rt.kill_pid(self.external_pid)
        self.sudo().write({
            'external_duration': self._external_elapsed(),
            'status': 'error' if not gone else 'done',
            'error_detail': False if gone else
                'External process %s could not be killed.' % self.external_pid,
        })
        _logger.info('session %s: aborted external pid %s (gone=%s)',
                     self.name, self.external_pid, gone)
        return gone

    def _external_elapsed(self):
        """Körningens varaktighet i sekunder (0 om ingen starttid finns)."""
        self.ensure_one()
        if not self.external_started_at:
            return 0.0
        delta = fields.Datetime.now() - self.external_started_at
        return round(delta.total_seconds(), 3)

    def _finalize_external_run(self, rss_kb=None):
        """Stäng en extern körning och skriv varaktigheten (D10).

        Anropas när processen observeras död — av livs-heartbeatet eller
        städ-cronen. RSS läses om den inte gavs (processen kan redan vara
        borta, då blir värdet None och lämnas orört).
        """
        self.ensure_one()
        from odoo.addons.ai_agent_core.core import runtime as rt
        if rss_kb is None and self.external_pid:
            rss_kb = rt.read_rss_kb(self.external_pid)
        vals = {'external_duration': self._external_elapsed()}
        if rss_kb:
            vals['external_rss_kb'] = rss_kb
        self.sudo().write(vals)
        return vals

    # ── Sessionsdomänen (coworker-session-smart-button D2) ─────────────
    # EN definition av "månaden", använd av BÅDE räknaren på ai.coworker
    # och det förvalda filtret i sökvyn. Två kopior glider isär när någon
    # rör tidszonen eller create_date-hanteringen — det var precis vad som
    # hände med `informative` i coworker-form-performance (Beslut 4).
    SESSION_PERIODS = ('all', 'this_month', 'last_month', 'last_7_days')

    @api.model
    def _session_period_start(self, period):
        """Startdatum för en namngiven period, eller None för 'all'.

        Serverns tidszon (Python `date.today()`), samma uttryck som
        `_token_totals_map` använder — `Sessions` och `Tokens` står bredvid
        varandra i knappraden och måste slå om vid samma månadsskifte.
        """
        if period not in self.SESSION_PERIODS:
            raise ValueError(
                'Unknown session period %r (valid: %s)'
                % (period, ', '.join(self.SESSION_PERIODS)))
        today = date.today()
        if period == 'all':
            return None
        if period == 'this_month':
            return date(today.year, today.month, 1)
        if period == 'last_month':
            return date(today.year - (today.month == 1),
                        (today.month - 2) % 12 + 1, 1)
        return today - timedelta(days=7)  # last_7_days

    @api.model
    def _session_period_end(self, period):
        """Exklusiv slutgräns för en period (endast 'last_month' har en)."""
        if period != 'last_month':
            return None
        return self._session_period_start('this_month')

    # Månadsnamnen som ett `Selection`-FÄLT, inte som `_()` på `strftime('%b')`.
    # Varför: `_()` på kodtermer kräver att `code_translations` laddar
    # modulens `sv.po` via `file_open` — och i drift trunkerar den läsningen
    # (92 kod-poster finns i filen, men `get_python_translations()` returnerar
    # 0 för modulen), så etiketten blev "Oct" i svensk UI. Ett
    # `Selection`-fält översätts via fältets jsonb-värde, som laddas av
    # moduluppgraderingen och bevisligen fungerar i drift.
    month_label = fields.Selection([
        ('Jan', 'Jan'), ('Feb', 'Feb'), ('Mar', 'Mar'), ('Apr', 'Apr'),
        ('May', 'May'), ('Jun', 'Jun'), ('Jul', 'Jul'), ('Aug', 'Aug'),
        ('Sep', 'Sep'), ('Oct', 'Oct'), ('Nov', 'Nov'), ('Dec', 'Dec'),
    ], string='Month', compute='_compute_month_label')

    def _compute_month_label(self):
        key = date.today().strftime('%b')
        valid = {k for k, _v in self._fields['month_label'].selection}
        value = key if key in valid else False
        for r in self:
            r.month_label = value

    @api.model
    def _month_label(self):
        """Aktuellt månadsnamn för knappetiketten, t.ex. "okt".

        Serverns tidszon (samma månad som `_session_period_start`
        beräknar). Översättningen hämtas ur `month_label`-fältets
        `Selection` — fältöversättning fungerar i drift där kodtermer inte
        gör det (se kommentaren ovan).
        """
        key = date.today().strftime('%b')
        field = self._fields['month_label']
        return dict(field._description_selection(self.env)).get(key, key)

    @api.model
    def _session_domain(self, coworker_id, period='all'):
        """Domän för en medarbetares sessioner inom en period.

        `'all'` ger bara medarbetaren — ingen datumgräns. Det är domänen
        smartknappen skickar, så att hela historiken går att nå; månaden
        ligger som ett borttagbart förvalt filter i stället
        (coworker-session-smart-button D1).
        """
        domain = [('coworker_id', '=', coworker_id)]
        start = self._session_period_start(period)
        if start:
            domain.append(('create_date', '>=', start))
        end = self._session_period_end(period)
        if end:
            domain.append(('create_date', '<', end))
        return domain

    @api.model
    def _live_external_count(self):
        """Antal levande externa agenter (D10 — mätpunkt, inte tröskel).

        Räknar sessioner med en PID som fortfarande lever. Ingen gräns
        jämförs mot detta tal: taket är empiriskt och sätts inte nu.
        """
        from odoo.addons.ai_agent_core.core import runtime as rt
        rows = self.sudo().search([('external_pid', '!=', False)])
        return len(rows.filtered(lambda s: rt.pid_alive(s.external_pid)))

    @api.model
    def _cron_reap_external(self, batch_size=200):
        """Livs-heartbeat + städning av föräldralösa processer (D6).

        Två fall:

        1. Sessionen är inte längre aktiv men processen lever → processen
           är föräldralös (Odoo tappade den, t.ex. vid omstart). Döda den.
        2. Processen är död men sessionen är aktiv → registrera felet så
           haveriet syns i data i stället för att se ut som en tom session.
        """
        from odoo.addons.ai_agent_core.core import runtime as rt
        sessions = self.sudo().search(
            [('external_pid', '!=', False)], limit=batch_size,
            order='id desc')
        # Timeout (D6): agenten får sin egen gräns via `--timeout`, men Odoo
        # äger livscykeln och måste också avbryta. Respit så att agentens
        # egen (snällare) avslutning hinner ske först.
        run_timeout = rt.get_int(
            self.env, rt.PARAM_RUN_TIMEOUT, rt.DEFAULT_RUN_TIMEOUT)
        grace = rt.get_float(
            self.env, rt.PARAM_ABORT_GRACE, rt.DEFAULT_ABORT_GRACE)
        limit = run_timeout + grace
        reaped, detected, timed_out = 0, 0, 0
        for sess in sessions:
            alive = rt.pid_alive(sess.external_pid)
            if alive and sess.status not in ('active', 'draft'):
                rt.kill_pid(sess.external_pid)
                sess._finalize_external_run()
                reaped += 1
                _logger.warning(
                    'external cleanup: killed orphan pid %s '
                    '(session %s, status %s)',
                    sess.external_pid, sess.name, sess.status)
            elif alive and sess.status in ('active', 'draft') \
                    and sess.external_started_at \
                    and sess._external_elapsed() > limit:
                rt.kill_pid(sess.external_pid)
                sess._finalize_external_run()
                sess.sudo().write({
                    'status': 'error',
                    'error_detail': 'The external run exceeded the time limit '
                                    '(%.0f s > %.0f s).' %
                                    (sess._external_elapsed(), limit),
                })
                timed_out += 1
                _logger.warning(
                    'external timeout: pid %s (session %s) ran %.0f s > '
                    '%.0f s — aborted',
                    sess.external_pid, sess.name,
                    sess._external_elapsed(), limit)
            elif not alive and sess.status in ('active', 'draft'):
                sess._finalize_external_run()
                sess.sudo().write({
                    'status': 'error',
                    'error_detail': 'External process %s ended unexpectedly.'
                                    % sess.external_pid,
                })
                detected += 1
                _logger.warning(
                    'external liveness heartbeat: pid %s is dead but session %s '
                    'is %s — marked error',
                    sess.external_pid, sess.name, sess.status)
        if reaped or detected or timed_out:
            _logger.info('external cleanup: %d orphans killed, %d '
                         'dead detected, %d timeouts',
                         reaped, detected, timed_out)
        return {'reaped': reaped, 'detected': detected,
                'timed_out': timed_out}

    # ── Watch-kö (fix-watch-async) ───────────────────────────────────
    # _trigger_watch skapar sessionen med watch_pending=True och returnerar
    # DIREKT — AI-körningen sker asynkront i cron (_process_watch_sessions)
    # i en egen transaktion, så en AI-failure kan aldrig abortera anroparens
    # transaktion (t.ex. mail-routning).
    watch_pending = fields.Boolean(
        'Watch Pending', default=False, index=True,
        help='Set by _trigger_watch — the session waits for asynchronous '
             'processing by cron (_process_watch_sessions).')
    watch_prompt = fields.Text(
        'Watch Prompt',
        help='Prompt generated by the watch trigger — run by cron.')
    watch_model = fields.Char(
        'Watch Model',
        help='Model name (record._name) that triggered the watch.')
    watch_res_id = fields.Integer(
        'Watch Record ID',
        help='Record id that triggered the watch.')

    # ── Heartbeat-kö (bevakning-over-tid D1/D2) ──────────────────────
    # Samma mönster som watch: väckaren skapar sessionen och returnerar,
    # processorn kör. Prompten är FRYST här (job_prompt) — ändras uppgiften
    # efter köning körs ändå den prompt som gällde när den köades.
    heartbeat_pending = fields.Boolean(
        'Heartbeat Pending', default=False, index=True,
        help='Set by the _heartbeat waker — the session waits for asynchronous '
             'processing by cron (_process_heartbeat_sessions).')
    job_prompt = fields.Text(
        'Job Prompt',
        help='The prompt built from the task when queued — frozen, run by the '
             'processor. Same role as watch_prompt but for heartbeat/cron.')

    config_json = fields.Text('Configuration')
    history_json = fields.Text('Message History')

    # Buzz session summary (change ai-orchestration-tidy-up 7.4)
    summary = fields.Text(
        'Session Summary',
        help='LLM-generated summary of the conversation — injected as context '
             'for new agents once the threshold is passed.')
    summary_message_count = fields.Integer(
        'Summary Message Count', default=0,
        help='Number of messages at the last summary.')

    memory_extracted = fields.Boolean(
        'Memory Extracted', default=False,
        help='Has the session experience been extracted to personal memory? '
             'Makes the bridge idempotent — the aftermath can be called from '
             'several places (mark_done, idle cron, buzz) without duplicate '
             'LLM calls.')

    token_input = fields.Integer('Input Tokens', default=0)
    token_output = fields.Integer('Output Tokens', default=0)
    token_sys = fields.Integer(
        'Systemtokens', compute='_compute_token_sys', store=False,
        help='Summan av alla session lines systemtokens (budget-hard-cap D7).')

    @api.depends('session_line_ids.token_sys')
    def _compute_token_sys(self):
        """Sessionens totala systemtoken-förbrukning (Σ lines)."""
        for r in self:
            r.token_sys = sum(l.token_sys or 0 for l in r.session_line_ids)

    # ── Kostnadskontext (session-cost-context) ──────────────────────────
    # Generiska fält (domän-rent): pi_session_id kopplar sessionen 1:1 till
    # en Pi-session (UUID). partner_id/cost_context_confirmed är grunden för
    # kostnadsuppföljning per kund. Domänfält (project_id/task_id) läggs av
    # bryggor (project_ai) via arv.
    pi_session_id = fields.Char(
        'Pi Session ID', index=True,
        help='The Pi session UUID (1:1 with the Pi session). Resuming the same '
             'Pi session finds the same Odoo session. On handover the field '
             'switches to the NEW holder — the history is in pi_session_ids.')
    pi_session_ids = fields.Char(
        'Pi Sessions (history)',
        help='Comma-separated list of every Pi session UUID that has worked in '
             'this Odoo session, in chronological order. Append-only: the '
             'first is the origin, the last is the current holder (same as '
             'pi_session_id). Handover is the only case where two Pi sessions '
             'share one Odoo session.')
    pi_message_count = fields.Integer(
        'Pi Messages Persisted', default=0, copy=False,
        help='How many entries in the Pi client messages[] are already stored '
             'as session lines. Pi sends the WHOLE history on every call; '
             'this counter means only NEW messages are persisted (one line '
             'per messages[] entry, no duplication).')
    partner_id = fields.Many2one(
        'res.partner', string='Customer', index=True,
        help='The customer (res.partner) the session cost is charged to. '
             'Normally derived from the project partner via the coworker '
             'cost context strategy.')
    cost_context_confirmed = fields.Boolean(
        'Cost Context Confirmed', default=False,
        help='Set once the cost attribution is confirmed (once per session). An '
             'already confirmed session does not ask again (not even after a '
             'resume/fork copy).')

    # ── Handover (pi-session-handover) ──────────────────────────────────
    # En användare avbryter en session på en dator och fortsätter på en
    # annan Pi. Tillståndet bor här (Odoo är auktoritativt och överlever
    # båda maskinerna). Fälten är LIFECYCLE — de får fyllas i efteråt,
    # medan sessionens rader förblir orörbara (append-only).
    #
    # Två former, och de skiljer sig i hur mycket information som finns:
    #   EXPLICIT (offered)  användaren bad om den. Ett LÖFTE — gäller
    #                       tills den startas igen, kan ligga i dygn.
    #                       Städas ALDRIG av cron.
    #   IMPLICIT            ingen state alls. En FRÅGA vid läsning:
    #                       "vilka sessioner slutade mitt i en tur?"
    #                       Se _handover_suggestions().
    handover_state = fields.Selection([
        ('none', 'Ingen'),
        ('offered', 'Offered — waiting to be claimed'),
        ('claimed', 'Claimed — being worked on'),
        ('done', 'Fulfilled'),
    ], string='Handover', default='none', index=True, tracking=True,
        help='Explicit handover: none -> offered -> claimed -> done. An offered '
             'handover is a promise and is never cleaned up by cron.')
    handover_note = fields.Text(
        'Handover Note',
        help='What was decided and what is pending ("next: fix the cert on '
             'gw1"). Written by the user, or derived from the aftermath with '
             '--auto. Injected as the first context when the session is '
             'claimed.')
    handover_mode = fields.Selection([
        ('note', 'Note — fresh context'),
        ('full', 'Full — the tool trace is available'),
    ], string='Handover Mode', default='note',
        help='note: the note is injected as the first context (small context '
             'window). full: the whole transcript is available plus the note — '
             'needed when the work was mid-flight (half-finished commands, '
             'debugging).')
    handover_at = fields.Datetime('Handover Requested', readonly=True)
    handover_by = fields.Many2one(
        'res.users', string='Handover By', readonly=True, index=True,
        help='The user who requested the handover. Handover always happens '
             'within the same user — handing over to another person is '
             'delegation, not handover.')
    handover_claimed_by = fields.Many2one(
        'res.users', string='Claimed By', readonly=True, index=True)
    handover_claimed_at = fields.Datetime('Claimed', readonly=True)

    # ── Sessionsnamn (session-title A→B→C) ──────────────────────────────
    # Namnet är det som visas i Pi:s /resume-lista och i Odoo:s trådvy.
    # Det sätts i tre steg, i den ordning informationen finns:
    #   A. _lookup_or_create_pi_session: första prompten (gratis, direkt).
    #   B. _maybe_update_title: EN mening, LLM-genererad efter första turen.
    #   C. _write_final_summary: eftermälet får förbättra namnet vid stängning.
    # title_source spårar vilket steg som senast satte namnet, så ett senare
    # steg kan uppgradera men aldrig ETT TIDIGARE steg skriva över ett senare.
    title_source = fields.Selection([
        ('prompt', 'First Prompt'),
        ('llm', 'LLM-genererad mening'),
        ('summary', 'Aftermath'),
        ('manual', 'Set manually'),
    ], string='Name Source', index=True, readonly=True,
        help='Which step last set the session name. Controls which later steps '
             'may overwrite it (prompt < llm < summary).')

    # ── Rekordkontext (ai-coworker-record-context) ──────────────────────
    # Vilken record — eller vilken markering — gällde DENNA tur. Fälten
    # sätts när sessionen skapas från en chatt-tur där en record eller en
    # list-markering kunde identifieras, och lämnas tomma annars.
    #
    # NAMNVAL (D1): `ai_`-prefixet matchar ai_task_id/ai_coworker_id och
    # undviker medvetet `context_*` (coworkerns injektionsinställningar)
    # samt env.context-nycklarna `_ai_context_model`/`_ai_context_id`.
    # De nycklarna betyder "aktuell SESSION" — inte "aktuell record" —
    # och läses av HITL, NATS-kontexten och sessionsminnena (D1b). Att
    # återanvända dem för en record kraschar HITL och tystar minnena.
    ai_record_model = fields.Char(
        'Record Model', index=True,
        help='Technical model name for the record (or the selection model) '
             'this turn applied to.')
    ai_record_id = fields.Integer(
        'Record ID',
        help='Id of the single record the turn applied to (form view). Empty '
             'when the turn applied to a selection — see ai_record_ids.')
    ai_record_ids = fields.Json(
        'Record IDs',
        help='List of ids for the selection the turn applied to (list view). '
             'Same pattern as server actions records.ids.')
    ai_record_json = fields.Text(
        'Record Fields',
        help='Serialized fields for the single record (singular). Prefers the '
             'frontend field values when present — they carry unsaved '
             'changes.')
    ai_records_json = fields.Text(
        'Records Fields',
        help='Serialized fields for the selection (plural). Field projection: '
             'the list view columns when the frontend sends them, otherwise a '
             'fixed projection (id, display_name, _rec_name).')
    ai_record_chatter = fields.Text(
        'Record Chatter',
        help='Chatter history for the single record. Excluded by default for '
             'collections — 35 records x 20 messages blows up the prompt '
             '(R7).')
    ai_record_count = fields.Integer(
        'Record Count', compute='_compute_ai_record_count', store=False,
        help='Number of ids in the selection (0 for singular).')

    @api.depends('ai_record_ids')
    def _compute_ai_record_count(self):
        for rec in self:
            ids = rec.ai_record_ids or []
            rec.ai_record_count = len(ids) if isinstance(ids, list) else 0

    def _set_record_context(self, record, front_end_info=None,
                            chatter=None):
        """Sätt singular rekordkontext på sessionen (idempotent).

        Args:
            record: Odoo-record turen gällde.
            front_end_info: Fältvärden från browsern (kan bära osparade
                ändringar). Föredras framför backend-serialisering.
            chatter: Färdig chatter-sträng. Serialiseras annars från
                recorden om den stöder _ai_serialize_messages_data().

        Tyst no-op vid fel — kontexten får aldrig fälla en körning.
        """
        self.ensure_one()
        if not record or not record.exists():
            return self
        vals = {
            'ai_record_model': record._name,
            'ai_record_id': record.id,
            'ai_record_ids': False,
            'ai_records_json': False,
        }
        # Fält: frontend-info vinner (osparade värden), annars backend.
        if front_end_info:
            vals['ai_record_json'] = (
                front_end_info if isinstance(front_end_info, str)
                else json.dumps(front_end_info, default=str,
                                ensure_ascii=False))
        else:
            try:
                vals['ai_record_json'] = record._ai_serialize_fields_data()
            except Exception as e:
                _logger.warning(
                    'session %s: field serialisation failed: %s',
                    self.id, e)
        # Chatter (singular tillåter den).
        if chatter is not None:
            vals['ai_record_chatter'] = chatter
        elif hasattr(record, '_ai_serialize_messages_data'):
            try:
                vals['ai_record_chatter'] = \
                    record._ai_serialize_messages_data()
            except Exception as e:
                _logger.warning(
                    'session %s: chatter-serialisering misslyckades: %s',
                    self.id, e)
        try:
            self.sudo().write(vals)
            _logger.info(
                'session %s: record context set — %s#%s (source: %s)',
                self.id, record._name, record.id,
                'frontend' if front_end_info else 'backend')
        except Exception as e:
            _logger.warning(
                'session %s: could not set record context: %s', self.id, e)
        return self

    def _set_records_context(self, records, fields=None,
                             max_records=None, include_chatter=False):
        """Sätt plural rekordkontext (markering) på sessionen (idempotent).

        Args:
            records: Recordset — markeringen.
            fields: Fältprojektion. När None används en fast projektion
                (id, display_name, _rec_name) — ALDRIG context_max_fields
                per record (D7: 35 x 100 fält spränger prompten).
            max_records: Gräns för antal records. Överskridandet loggas
                och rapporteras i prompten — det får inte tystas (R5).
            include_chatter: Chatter är AVSTÄNGT som default för samlingar.

        Tyst no-op vid fel.
        """
        self.ensure_one()
        if not records:
            return self
        model = records._name
        all_ids = list(records.ids)
        truncated = False
        if max_records and len(all_ids) > max_records:
            truncated = True
            all_ids = all_ids[:max_records]
        recs = records.browse(all_ids)
        # Fältprojektion: explicit lista, annars fast projektion.
        if not fields:
            fields = ['id', 'display_name']
            rec_name = recs._rec_name
            if rec_name and rec_name not in fields:
                fields.append(rec_name)
        try:
            data = recs.read(fields)
        except Exception as e:
            _logger.warning(
                'session %s: could not read the selection: %s', self.id, e)
            return self
        vals = {
            'ai_record_model': model,
            'ai_record_id': False,
            'ai_record_ids': all_ids,
            'ai_records_json': json.dumps(
                data, default=str, ensure_ascii=False),
            'ai_record_json': False,
            'ai_record_chatter': False,
        }
        if include_chatter and hasattr(recs, '_ai_serialize_messages_data'):
            try:
                vals['ai_record_chatter'] = '\n'.join(
                    r._ai_serialize_messages_data() for r in recs)
            except Exception as e:
                _logger.warning(
                    'session %s: chatter for the selection failed: %s',
                    self.id, e)
        try:
            self.sudo().write(vals)
            _logger.info(
                'session %s: markering satt — %s x%d%s',
                self.id, model, len(all_ids),
                ' (TRUNCATED from %d)' % len(records) if truncated else '')
        except Exception as e:
            _logger.warning(
                'session %s: could not set the selection: %s', self.id, e)
        return self

    def _session_capture_context(self):
        """Domän-ren hook: bryggor (t.ex. project_ai) override:ar för att
        fånga domänkontext (project/task) på sessionen vid körning.

        Kallas av openai_api-vägen efter att en session skapats/återfunnits.
        Default: ingen åtgärd (core förblir domän-rent).
        """
        return self

    def _session_auto_capture(self, prompt):
        """Domän-ren hook: deterministisk kontextfångst ur användarens
        prompt (t.ex. 'task 36779' → task/projekt/kund).

        Kallas av openai_api-vägen efter att sessionen skapats/återfunnits
        OCH efter _session_capture_context, innan LLM-körningen. Bryggor
        (t.ex. project_ai) override:ar med regex/heuristik — core förblir
        domän-rent. Default: ingen åtgärd.
        """
        return self

    def _apply_cost_context_strategy(self):
        """Anropa coworkerns resolver-strategi för att härleda partner_id
        (och ev. domänfält) ur sessionens nuvarande kontext.

        Strategin väljs av coworkerns `cost_context_partner_strategy`
        (t.ex. "project_partner" registrerad av project_ai). Tyst no-op om
        ingen strategi är satt/registrerad — hooks får aldrig kasta.
        """
        self.ensure_one()
        strategy = self.coworker_id.cost_context_partner_strategy \
            if self.coworker_id else ''
        if not strategy:
            return self
        fn = get_cost_context_strategy(strategy)
        if fn is None:
            _logger.warning('cost-context strategy %r not registered',
                            strategy)
            return self
        try:
            fn(self)
        except Exception as e:
            _logger.warning('cost-context strategy %s failed: %s',
                            strategy, e)
        return self

    @api.model
    def _lookup_or_create_pi_session(self, pi_session_id,
                                     copy_from_pi_session_id='',
                                     prompt='', start_reason=''):
        """Find-or-create session via pi_session_id (session-cost-context).

        Används av POST /ai/v1/sessions/lookup och openai_api-vägen.
        Idempotent: samma pi_session_id → samma session. Vid
        copy_from_pi_session_id (fork) skapas en NY session med kontexten
        (project/task/partner + cost_context_confirmed) kopierad från
        källsessionen.

        Namnet (session-title steg A): när `prompt` skickas med döps
        sessionen efter den i stället för efter UUID:ns första åtta tecken.
        Ett namn ur UUID:t gick inte att skilja från någon annan session i
        Pi:s /resume-lista. Saknas prompt faller vi tillbaka på det gamla
        beteendet — en session utan namn är bättre än en krasch.

        Returnerar (session, created: bool).
        """
        pi_session_id = (pi_session_id or '').strip().lower()
        session = self.search(
            [('pi_session_id', '=', pi_session_id)], limit=1)
        if session:
            # Resume: en senare tur kan ha en bättre prompt än den första,
            # men aldrig bättre än ett LLM-namn (rangordningen i
            # _maybe_set_title avgör).
            if prompt:
                session._maybe_set_title(prompt, source='prompt')
            # En stängd session som återupptas ska bli aktiv igen — annars
            # ser den ut att vara avslutad samtidigt som den tar emot turer
            # (session-observability).
            session._reopen_if_closed(reason='resume')
            return session, False
        title = self._clean_title(prompt) if prompt else ''
        vals = {
            'pi_session_id': pi_session_id,
            'status': 'active',
            'init_type': 'openai_api',
            # Klientens `session_start.reason` (session-observability).
            # Default `api`: vägen hit är alltid ett API-anrop, och `fork`
            # sätts av anroparen när kontexten kopieras.
            'start_reason': start_reason or (
                'fork' if copy_from_pi_session_id else 'api'),
            # Fallback när prompt saknas eller inte ger ett namn
            # (chat-session-limits-and-names D4): ett läsbart namn, inte ett
            # uuid-fragment. `pi_session_id[:8]` gav trådnamn som `01a1ac59`
            # — 1 305 sessioner i drift hade det. Ett id-baserat namn är bara
            # sista utväg när det inte finns något att bygga ur.
            'name': title or ('API-session ' + pi_session_id[:8]
                              if pi_session_id else 'API-session'),
            'title_source': 'prompt' if title else False,
            'user_id': self.env.user.id,
        }
        if copy_from_pi_session_id:
            src = self.search(
                [('pi_session_id', '=', copy_from_pi_session_id)], limit=1)
            if src:
                for f in ('project_id', 'task_id', 'partner_id'):
                    if f in self._fields:
                        vals[f] = src[f].id if src[f] else False
                if 'cost_context_confirmed' in self._fields:
                    vals['cost_context_confirmed'] = \
                        src.cost_context_confirmed
        return self.create(vals), True

    def _capture_context(self, task=None, project=None, partner=None,
                         object_ref=None, **kwargs):
        """Domän-ren skrivpunkt för domänkontext (no-op-ankare).

        Bryggor (project_ai, prd_ai) override:ar och anropar super() så
        alla domäner samlas i MRO-kedjan. Core skriver inget — det vet
        inte vilka domänfält som finns. Default: ingen åtgärd.
        """
        return self

    # ── Sessionsminne (improve-ai-coworker-memory-and-tools 1.1–1.3) ────
    # Sessionens rader är den AUKTORITATIVA kontextkällan: historiken
    # rekonstrueras deterministiskt från session_line_ids i ordning — aldrig
    # från en tillfällig minnesbild eller en separat räknare. Räknaren
    # (pi_message_count) är ett DERIVAT och används bara för att veta hur
    # mycket av Pi:s messages[] som redan persisterats.

    def _build_history_from_lines(self, exclude_last_assistant=False):
        """Bygg core.Message-historik från sessionens rader (i ordning).

        Returnerar list[core.Message] med roller user/assistant/tool/system,
        inkl. assistant-tool_calls och tool-resultat bevarade som par (krävs
        av providern vid replay). Tool-rader får tool_name som name så
        OpenAI-formatet håller.

        Verktygsanrop hanteras i två format: OpenAI-format (med 'function')
        blir replaybara ``tool_calls``; preview-format (name/preview) läggs
        som en läsbar sammanfattning i assistant-radens content, så modellen
        ser *att* och *vilket* verktyg som anropades även när anropet inte
        kan spelas upp.

        Args:
            exclude_last_assistant: hoppa över sista assistant-raden —
                används när svaret skrivs direkt och nästa anrop skickar
                samma post i sitt delta (dedup).
        """
        self.ensure_one()
        from odoo.addons.ai_agent_core.core.provider import Message, Role
        lines = self.session_line_ids.sorted(
            key=lambda l: (l.sequence or 0, l.id or 0))
        if exclude_last_assistant and lines:
            last = lines[-1]
            if last.role == 'assistant':
                lines = lines[:-1]
        role_map = {
            'user': Role.USER,
            'assistant': Role.ASSISTANT,
            'tool': Role.TOOL,
            'system': Role.SYSTEM,
        }
        history = []
        # Vilka tool_call_id:n som den senaste assistant-raden deklarerade.
        # En tool-rad får bara skickas om dess id finns här — annars är den
        # föräldralös och OpenAI-formatet ogiltigt (422 hos strikta providers).
        declared_call_ids = set()
        for line in lines:
            role = role_map.get(line.role)
            if role is None:
                continue
            tool_calls = None
            tool_summary = ''
            if line.tool_calls:
                try:
                    parsed = json.loads(line.tool_calls)
                    if isinstance(parsed, list) and parsed \
                            and isinstance(parsed[0], dict):
                        if 'function' in parsed[0]:
                            # OpenAI-format: fullt replaybart.
                            tool_calls = parsed
                        elif 'name' in parsed[0]:
                            # Preview-format (name/preview) — det format
                            # web_ui-vägen skriver. Kan inte replayas, men
                            # modellen ska se ATT och VILKET verktyg som
                            # anropades (web-ui-session-kontext D2).
                            tool_summary = self._tool_calls_summary(parsed)
                except (ValueError, TypeError):
                    tool_calls = None
                    tool_summary = ''
            if role == Role.TOOL:
                call_id = (line.tool_call_id or '').strip()
                # Säkerhetsnät (2026-10-08): en tool-rad utan tool_call_id —
                # eller med ett id som ingen assistant-rad deklarerat — gör
                # hela historiken ogiltig och får strikta providers att
                # avvisa anropet med 422. Raden kan inte repareras i
                # efterhand (gamla sessioner saknar id), så vi utelämnar den
                # hellre än att fälla hela turen.
                if not call_id or call_id not in declared_call_ids:
                    continue
                history.append(Message(
                    role=Role.TOOL,
                    content=line.content or '',
                    name=line.tool_name or '',
                    # tool_call_id krävs av OpenAI-formatet: en tool-rad måste
                    # paras med assistant-radens tool_calls[].id.
                    tool_call_id=call_id,
                ))
            elif role == Role.ASSISTANT:
                content = line.content or ''
                if tool_summary:
                    content = (content + '\n\n' + tool_summary).strip()
                # Nya assistant-raden äger sina tool_calls — nollställ först
                # så att tool-rader bara paras mot den rad som deklarerade dem.
                declared_call_ids = set()
                if tool_calls:
                    for tc in tool_calls:
                        cid = (tc.get('id') or '').strip() \
                            if isinstance(tc, dict) else ''
                        if cid:
                            declared_call_ids.add(cid)
                history.append(Message(
                    role=Role.ASSISTANT,
                    content=content,
                    tool_calls=tool_calls,
                ))
            else:
                history.append(Message(role=role, content=line.content or ''))
        return history

    @staticmethod
    def _tool_calls_summary(parsed):
        """Gör preview-formatets verktygsanrop till en läsbar sammanfattning.

        Preview-poster (``{'name':…, 'preview':…}``) kan inte spelas upp som
        OpenAI-anrop, men de bär vilka verktyg som användes och en preview av
        resultaten. Att hoppa över dem (som tidigare) kastade bort
        verktygsspåret helt, så modellen kunde inte relatera till vad den
        redan gjort.
        """
        names = []
        for item in parsed:
            if not isinstance(item, dict):
                continue
            name = str(item.get('name') or '').strip()
            if not name:
                continue
            preview = str(item.get('preview') or '').strip()
            names.append('%s: %s' % (name, preview[:120]) if preview else name)
        if not names:
            return ''
        return '[Verktygsanrop i denna tur: %s]' % '; '.join(names)

    # ── Relevansbedömning av sessionhistoriken ───────────────────────
    # (session-history-relevance 2.x) Historiken injicerades tidigare blint
    # som de sista N meddelandena. Det gjorde att irrelevanta turer kunde
    # tränga ut relevanta. Här väljs i stället turer mot den aktuella
    # frågan: senaste turen behålls alltid, äldre rankas deterministiskt
    # (lexikal överlapp — ingen LLM, ingen latens i kritisk väg).

    @staticmethod
    def _history_turns(history):
        """Dela historiken i turer (user → nästa user).

        Returnerar en lista av (start_index, end_index_exklusiv, turn).
        Meddelanden före första user-raden (t.ex. ett system-snapshot)
        blir sin egen inledande grupp.
        """
        turns = []
        start = 0
        for i, m in enumerate(history):
            role = getattr(m.role, 'value', m.role)
            if role == 'user' and i > start:
                turns.append((start, i, history[start:i]))
                start = i
        if start < len(history):
            turns.append((start, len(history), history[start:]))
        return turns

    @staticmethod
    def _history_tokens(text):
        """Låg-normaliserade ord (>=3 tecken) för lexikalt överlapp."""
        import re
        return set(re.findall(r'\w{3,}', (text or '').lower()))

    def _select_history_for_query(self, history, query, keep_last=1,
                                  budget=40):
        """Välj historik mot frågan (session-history-relevance D2).

        Senaste `keep_last` turerna behålls alltid. Äldre turer rankas mot
        `query` och tas med i fallande relevans tills `budget` meddelanden
        nåtts (räknat i meddelanden, inte turer). En tur vars ord överlappar
        frågan behålls framför en utan överlapp.

        Deterministisk och billig: ingen LLM, ingen extern sökning — den
        får inte ligga i den kritiska vägen (TTFT).

        Args:
            history: list[Message] (sessionens auktoritativa historik)
            query: aktuell fråga (prompt)
            keep_last: antal senaste turer som alltid behålls
            budget: max antal meddelanden i det valda resultatet
        """
        history = list(history or [])
        if not history:
            return []
        turns = self._history_turns(history)
        if not turns:
            return history
        q_tokens = self._history_tokens(query)

        # Senaste turerna behålls alltid.
        n = len(turns)
        keep_from = max(0, n - max(1, keep_last))
        kept = [turns[i] for i in range(keep_from, n)]
        older = [turns[i] for i in range(0, keep_from)]

        def _overlap(turn):
            text = ' '.join(getattr(m, 'content', '') or '' for m in turn[2])
            return len(q_tokens & self._history_tokens(text))

        # Äldre turer: högst överlapp först, därefter senast (stabil ordning).
        older_ranked = sorted(
            older, key=lambda t: (-_overlap(t), -t[0]))

        selected = list(kept)
        used = sum(end - start for start, end, _ in kept)
        for turn in older_ranked:
            size = turn[1] - turn[0]
            if used + size > budget:
                continue
            selected.append(turn)
            used += size

        # Återställ ursprunglig ordning så historiken förblir kronologisk.
        selected.sort(key=lambda t: t[0])
        result = []
        for _, _, turn in selected:
            result.extend(turn)
        # session-history-relevance 2.3: logga urvalet så beteendet går att
        # följa i drift (hur många turer som behölls/uteslöts).
        _logger.info(
            'session %s: history selection %s/%s turns, %s/%s messages '
            '(question: %s characters)',
            self.id, len(selected), len(turns), len(result), len(history),
            len(query or ''))
        return result

    def _sync_pi_message_count(self):
        """Avstäm pi_message_count mot antalet faktiska meddelanderader.

        Räknaren är en delta-kursor mot Pi:s messages[] och är ett DERIVAT
        av raderna — aldrig tvärtom.

        Viktigt: räknaren LÄMNAS orörd här (pi-message-persist-dedup D2).
        Att sätta den till radantalet — i någon riktning — gör nästa anrops
        delta fel: Pi kompakterar sin historik, så listan kan bli KORTARE än
        kursorn, och en sänkning fick hela historiken att skrivas om (mätt:
        115 omskrivningar av samma prompt i en session, 224 985 identiska
        rader i drift). Avvikelsen loggas så den syns; sanningen bärs av
        `_filter_known_messages` som jämför content_hash.
        """
        self.ensure_one()
        # pi-message-persist-dedup D2: kursorn är bara en SNABBVÄG. Den får
        # inte sänkas här — en sänkning gör nästa anrops delta fel och skriver
        # om historiken (det var precis buggen: 115 omskrivningar av samma
        # prompt i en session). Är kursorn ogiltig hanterar
        # `_filter_known_messages` det med hash-jämförelse i stället.
        #
        # Kvar: avvikelsen LOGGAS så den syns, men raderna rör inte kursorn.
        actual = len(self.session_line_ids.filtered(
            lambda l: l.role in ('user', 'assistant', 'tool', 'system')))
        stored = int(self.pi_message_count or 0)
        if stored > actual:
            _logger.info(
                'session %s: pi_message_count %s > %s lines — cursor left '
                'alone (hash dedup holds the truth)', self.id, stored, actual)
        if stored < actual:
            _logger.debug(
                'session %s: pi_message_count %s < %s lines (delta cursor, '
                'expected for directly written lines)', self.id, stored,
                actual)
        return stored

    def _save_client_response(self, content, role='assistant', model_real='',
                              token_input=0, token_output=0, debug_info='',
                              source_urls='', tool_calls='', sys_multiplier=1.0):
        """Spara klientens svarspost — idempotent mot serverns persistens.

        Servern persisterar turens avslutande assistantsvar själv
        (web-ui-stream-turn-persistens 2.1). Klienten POST:ar ändå samma
        svar; utan dedup skulle assistantsvaret dubbleras. Skapar därför
        ingen ny assistant-rad om den senaste raden redan är ett
        assistantsvar med identiskt innehåll — samma mönster som
        `_persist_pi_messages` (dedup mot sista persisterade raden).

        Ett ANNAT svar appendas normalt (t.ex. en nyare formulering).
        Returnerar den skapade raden, eller en tom recordset vid dedup.

        ── Skrivregeln "en rad bokförs en gång" (3.4) ────────────────────
        Klienten får berika en rad — aldrig bokföra samma tokens två
        gånger. Servern bokför numera turens verkliga usage på
        assistant-raden (`_persist_stream_answer`, restore-webui-token-
        usage 3.2). Klientens POST skickar dessutom ett eget
        `token_output` (frontendens `lastUsageOutput`). Utan denna grind
        kunde samma turs output läggas på två rader → sessionens
        `token_sys` (och därmed budgettaket) blåstes upp.

        Regeln: om den senaste assistant-raden redan är mätt
        (`usage_reported = True`) meddelar vi klienten att usage är
        serverägd — svaret bokförs ändå (debug/sources/tool_calls), men
        tokenfälten lämnas orörda.
        """
        self.ensure_one()
        Line = self.env['ai.coworker.session.line'].sudo()
        last = Line.search(
            [('session_id', '=', self.id)],
            order='sequence desc, id desc', limit=1)
        if (role == 'assistant' and last and last.role == 'assistant'
                and (last.content or '') == (content or '')):
            return Line.browse(0)
        # Serverns rad vinner när DEN redan skrivit turens assistantsvar
        # (webui-show-work-again 5.3). Sedan `_persist_stream_answer` började
        # skriva källor, debug och verktygsanrop (5.1) är serverns rad den
        # fullständiga — klientens POST är en berikning som ofta kommer med
        # ett annat `content` (t.ex. tomt svar mot `(inget svar)`) och därför
        # INTE fångas av jämförelsen ovan. Utan den här grinden blev det en
        # andra assistant-rad för samma tur: arbetet dubblerat, och trådvyn
        # visade två svar.
        #
        # MEN grinden får inte svälja ett GENUINT annat svar: `_save_client_
        # response` används även för att appenda ett nyare svar (test 6.3 i
        # webui-token-display). Skillnaden är turens EGEN POST kontra en
        # separat post: klientens POST för SAMMA tur skickar serverns `content`
        # eller en tom sträng (den POST:ar bara berikningen), medan ett annat
        # svar har eget innehåll som skiljer sig från serverns rad.
        #
        # Regeln: berika bara när POST:ens `content` är tomt eller ryms i
        # serverns rad. Annars är det ett nytt svar och ska appendas.
        _server_content = (last.content or '').strip() if (
            last and last.role == 'assistant') else ''
        _post_content = (content or '').strip()
        _same_turn = (not _post_content) or (
            bool(_server_content) and (
                _post_content == _server_content
                or _post_content in _server_content
                or _server_content in _post_content))
        if (role == 'assistant' and last and last.role == 'assistant'
                and last.usage_reported and _same_turn):
            updates = {}
            # Berika bara tomma fält — skriv aldrig över med tomt.
            for fname, value in (
                    ('debug_info', debug_info),
                    ('source_urls', source_urls),
                    ('tool_calls', tool_calls)):
                if value and not (getattr(last, fname) or '').strip():
                    updates[fname] = value
            if updates:
                try:
                    # `write()` nekar metadatafälten (append-only-skyddet,
                    # _IMMUTABLE_FIELDS). `_enrich_from_client` är den enda
                    # legitima vägen: den rör bara tomma metadatafält och
                    # kan aldrig skriva över serverns värden.
                    last.sudo()._enrich_from_client(updates)
                except Exception:
                    pass  # best-effort: berikningen får inte fälla turen
            return Line.browse(0)
        # Servern äger bokföringen: en redan mätt assistant-rad får inte
        # skrivas om med klientens (potentiellt dubbelräknade) siffror.
        if (role == 'assistant' and last and last.role == 'assistant'
                and last.usage_reported):
            token_input = 0
            token_output = 0
        seq = 0
        if last:
            seq = (last.sequence or 0) + 1
        return Line.create({
            'session_id': self.id,
            'sequence': seq,
            'role': role,
            'content': content,
            'debug_info': debug_info,
            'source_urls': source_urls,
            'tool_calls': tool_calls,
            'token_input': token_input,
            'token_output': token_output,
            'model_real': model_real,
            'sys_multiplier': sys_multiplier,
        })

    # ── Kontinuitet (find-or-create med fallback) ──────────────────────
    # Används av /ai/v1/chat/completions + openai_api-vägen. Så länge en
    # Pi-session lever (och skickar pi_session_id/session_id) återfinns
    # samma Odoo-session. Skulle klienten av någon anledning INTE skicka
    # något id (t.ex. delegering via en sub-agent som inte propagerar
    # pi_session_id) faller vi tillbaka på närmast nyligen aktiva session
    # för samma (coworker, user) — så konversationen inte fragmenteras i
    # en ny session per tur.
    @api.model
    def _find_or_create_coworker_session(self, coworker_id, user_id,
                                         pi_session_id='', session_id=0,
                                         prompt='', idle_minutes=20):
        """Find-or-create en coworker-session (1:1 mot Pi-sessionen).

        Prioritet:
          1. Exakt session_id (om giltig).
          2. pi_session_id (1:1 mot Pi-sessionen).
          3. Fallback (ENDART när inget pi_session_id finns): närmast
             nyligen aktiva session för samma (coworker_id, user_id) vars
             write_date ligger inom idle_minutes — fortsätt den i stället
             för att skapa ny. Gäller icke-Pi-klienter (web_ui, mail m.fl.)
             som saknar Pi-session.
          4. Annars: skapa en ny aktiv session.

        VIKTIGT (session-cost-context 8.5): när ett pi_session_id SKICKAS
        men ingen session med det id:t finns, skapas ALLTID en ny session —
        vi faller aldrig tillbaka på "närmast aktiva" (det skulle knyta en
        ny Pi-session till en annan Pi-sessions Odoo-session och tappa
        1:1-kopplingen). Fallbacken är alltså reserverad för klienter som
        inte har något Pi-id alls.

        Returnerar (session, created: bool).
        """
        session = self.browse(0)
        if session_id:
            session = self.browse(int(session_id))
            if not session.exists():
                session = self.browse(0)
        pi_session_id = (pi_session_id or '').strip().lower()
        if not session and pi_session_id:
            session = self.search(
                [('pi_session_id', '=', pi_session_id)], limit=1)
            if session:
                # Självläkning: en session som hittats via pi_session_id men
                # saknar init_type (t.ex. skapad av en äldre kodväg) märks
                # som openai_api så filtrering/statistik blir korrekt.
                if not session.init_type:
                    session.sudo().write({'init_type': 'openai_api'})
                # Stängd men återupptagen → aktiv igen (session-observability).
                session._reopen_if_closed(reason='resume')
                # Självläkning (kostnadskontext-injektion-stream 1.1): en
                # session skapad via sessions-lookup bär ingen coworker
                # (coworkern är inte vald vid Pi:s session_start). Utan
                # backfill förblir sessionen permanent kontextlös och
                # kostnadskontexten kan aldrig härledas.
                session._backfill_coworker(coworker_id)
                return session, False
            # pi_session_id skickat men okänt → NY session (ingen fallback).
            return self.create({
                'coworker_id': coworker_id,
                'status': 'active',
                'init_type': 'openai_api',
                'start_reason': 'api',
                'name': (prompt or 'API')[:80],
                'user_id': int(user_id or 0),
                'pi_session_id': pi_session_id,
            }), True
        if not session and coworker_id:
            # Fallback (endast utan pi_session_id): fortsätt närmast nyligen
            # aktiva session (samma coworker + user) i stället för att
            # fragmentera. Icke-Pi-klienter (web_ui/mail/powerbox).
            cutoff = fields.Datetime.now() - timedelta(minutes=idle_minutes)
            session = self.search([
                ('coworker_id', '=', int(coworker_id)),
                ('user_id', '=', int(user_id or 0)),
                ('status', '=', 'active'),
                ('write_date', '>=', cutoff),
            ], limit=1, order='write_date desc')
        if session:
            session._backfill_coworker(coworker_id)
            return session, False
        return self.create({
            'coworker_id': coworker_id,
            'status': 'active',
            'init_type': 'openai_api',
            'start_reason': 'api',
            'name': (prompt or 'API')[:80],
            'user_id': int(user_id or 0),
        }), True

    def _backfill_coworker(self, coworker_id):
        """Sätt coworker_id på en återfunnen session som saknar det.

        Sessioner skapade via sessions-lookup (`_lookup_or_create_pi_session`)
        bär ingen coworker — vid Pi:s `session_start` är coworkern ännu inte
        vald. Utan backfill blir sessionen permanent kontextlös och
        kostnadskontexten kan inte härledas (kostnadskontext-injektion-stream
        1.1).

        Skriver ENDAST när fältet är tomt, så en känd coworker aldrig skrivs
        över (1.2). Tyst no-op vid fel — får aldrig fälla en körning.
        """
        self.ensure_one()
        if not coworker_id or self.coworker_id:
            return self
        try:
            self.sudo().write({'coworker_id': int(coworker_id)})
            _logger.info(
                'session %s: coworker_id backfillat till %s',
                self.id, coworker_id)
        except Exception as e:
            _logger.warning(
                'session %s: kunde inte backfilla coworker_id: %s',
                self.id, e)
        return self

    # ── Sessionsnamn (session-title A→B→C) ────────────────────────────
    #
    # VARFÖR: en session skapad via sessions-lookup hette `pi_session_id[:8]`
    # — åtta hextecken. I Pi:s /resume-lista och i Odoo:s trådvy gick den
    # inte att skilja från någon annan session. Namnet ska vara EN MENING
    # som beskriver vad sessionen handlar om.
    #
    # Stegen är ordnade efter när informationen finns, inte efter kvalitet:
    #   A. Första prompten — finns redan vid sessionens skapande, gratis.
    #   B. EN mening via LLM — kräver att minst en tur körts.
    #   C. Eftermälets första rad — finns först vid stängning/idle.
    #
    # Ett senare steg får uppgradera ett tidigare, men ett tidigare steg får
    # ALDRIG skriva över ett senare (annars skulle t.ex. varje ny tur i en
    # resume:ad session nolla ett LLM-namn tillbaka till prompten).
    # Det är vad `title_source`-ordningen nedan upprätthåller.

    #: Rangordning för namnkällor. Högre siffra = starkare anspråk.
    TITLE_SOURCE_RANK = {
        'prompt': 1,
        'llm': 2,
        'summary': 3,
        'manual': 4,
    }

    #: Max antal tecken i ett genererat namn. Pi:s /resume-kolumn är smal
    #: och Odoo:s trådvy klipper ändå — en mening ryms i 80.
    TITLE_MAX_CHARS = 80

    def _title_rank(self):
        """Rang för sessionens nuvarande namnkälla (0 = inget namn)."""
        self.ensure_one()
        if not self.title_source:
            return 0
        return self.TITLE_SOURCE_RANK.get(self.title_source, 0)

    @api.model
    def _clean_title(self, text, max_chars=None):
        """Normalisera en namnkandidat till EN rad, utan skräp.

        Tar bort markdown-rubriker, citattecken, punktlistor och
        radbrytningar — namnet ska vara en mening, inte ett dokument.
        Returnerar '' om inget användbart återstår.
        """
        if not text:
            return ''
        max_chars = max_chars or self.TITLE_MAX_CHARS
        line = str(text).strip()
        # Första icke-tomma raden (LLM:er lägger gärna en rubrik först).
        for candidate in line.splitlines():
            candidate = candidate.strip()
            if candidate:
                line = candidate
                break
        # Markdown-rubrik, listmarkör, fetstil och citattecken.
        line = re.sub(r'^#{1,6}\s*', '', line)
        line = re.sub(r'^[-*•]\s+', '', line)
        line = line.replace('**', '').replace('__', '')
        line = line.strip().strip('"\'“”‘’')
        # Rubrik-prefix som "Syfte:" eller "Titel:" hör inte i namnet.
        #
        # OBS (chat-session-limits-and-names): listan måste omfatta
        # eftermälets EGNA rubriker — `SUMMARY_HEADINGS`. Utan dem strippades
        # inte `Syfte:` alls, och `Syfte` utan kolon blev ett godkänt namn.
        _prefix_words = (
            'titel', 'title', 'subject', 'amne', 'subject', 'sammanfattning',
            'syfte', 'utfall', 'beslut', 'fakta', 'artefakter')
        line = re.sub(
            r'^(%s)\s*:\s*' % '|'.join(_prefix_words),
            '', line, flags=re.IGNORECASE)
        line = re.sub(r'\s+', ' ', line).strip()
        # Ett namn som BARA är ett rubrikord säger ingenting om sessionen
        # (chat-session-limits-and-names D2). Eftermälet skriver `## Syfte`,
        # och rubrikstrippningen ovan tar `##` men lämnar ordet kvar — vilket
        # gav sessionsnamnet "Syfte". Returnera tomt: `_maybe_set_title`
        # hanterar det och en starkare källa får sätta namnet i stället.
        _headings = {h.lower() for h in self.SUMMARY_HEADINGS}
        if line.rstrip(':').strip().lower() in _headings:
            return ''
        if len(line) > max_chars:
            line = line[:max_chars].rstrip()
        return line

    @api.model
    def _title_from_summary(self, summary):
        """Första meningsfulla meningen ur ett eftermäle (D3).

        Eftermälet är strukturerat med fasta rubriker (`## Syfte`, `## Utfall`
        …). Namnet ska beskriva sessionen, så vi hoppar rubriker, tomma rader
        och rubrikord och tar första raden som bär innehåll.

        Returnerar '' om inget användbart finns — `_maybe_set_title` hanterar
        det och en starkare källa får sätta namnet.
        """
        if not summary:
            return ''
        headings = {h.lower() for h in self.SUMMARY_HEADINGS}
        for raw in str(summary).splitlines():
            line = raw.strip()
            if not line:
                continue
            # Markdown-rubrik, listmarkör och fetstil bort.
            line = re.sub(r'^#{1,6}\s*', '', line)
            line = re.sub(r'^[-*•]\s+', '', line)
            line = line.replace('**', '').replace('__', '').strip()
            if not line:
                continue
            # Rubrikord (med eller utan kolon) hoppas över.
            if line.rstrip(':').strip().lower() in headings:
                continue
            # En rad som bara är ett ord är oftast en etikett, inte en mening.
            if len(line.split()) < 2:
                continue
            return line
        return ''

    def _maybe_set_title(self, text, source='prompt', force=False):
        """Sätt sessionens namn om källan är starkare än nuvarande.

        Idempotent och tyst: ett svagare steg (t.ex. prompt vid varje ny tur
        i en resume:ad session) rör inte ett starkare namn. Returnerar True
        om namnet faktiskt skrevs.
        """
        self.ensure_one()
        if not force and self.TITLE_SOURCE_RANK.get(source, 0) <= \
                self._title_rank():
            return False
        title = self._clean_title(text)
        if not title:
            return False
        try:
            self.sudo().write({'name': title, 'title_source': source})
            _logger.info(
                'session %s: namn satt av %s → %r',
                self.id, source, title)
            return True
        except Exception as e:
            _logger.warning(
                'session %s: could not set the name (%s): %s',
                self.id, source, e)
            return False

    def _maybe_update_title(self, force=False):
        """Steg B: generera EN mening som namn på sessionen.

        Anropas i bakgrunden efter en avslutad tur (samma mönster som
        `_maybe_learn_async`). Kräver minst en user- och en assistant-rad —
        annars finns inget att döpa efter. Idempotent: när ett LLM-namn
        redan satts görs inget nytt anrop.

        Returnerar namnet (str) eller None.
        """
        self.ensure_one()
        if not force and self._title_rank() >= \
                self.TITLE_SOURCE_RANK['llm']:
            return self.name
        lines = self.session_line_ids.sorted('sequence')
        if not any(l.role == 'user' for l in lines) or \
                not any(l.role == 'assistant' for l in lines):
            return None
        transcript = self._final_summary_transcript(lines, max_chars=6000)
        if not transcript.strip():
            return None
        title = self._run_title_llm(transcript)
        if not title:
            return None
        if self._maybe_set_title(title, source='llm', force=force):
            return self.name
        return None

    def _title_prompt(self, transcript):
        """Prompt för namngenereringen — EN mening, på svenska."""
        return (
            'Give the session below a name in Swedish. Answer with EXACTLY one '
            'sentence, at most %d characters, with no heading, no quotation '
            'marks and no trailing period. The sentence should describe what '
            'the session is about, not what was said first.\n\n'
            '--- CONVERSATION ---\n%s' % (self.TITLE_MAX_CHARS, transcript))

    def _run_title_llm(self, transcript):
        """Kör LLM-anropet för namnet. Returnerar str eller None.

        Avskilt från `_maybe_update_title` så att flödet kan testas utan
        att röra providern (samma mönster som `_run_final_summary_llm`).
        """
        import asyncio
        try:
            from odoo.addons.ai_agent_core.core.provider import (
                Message, ProviderFactory, Role, _run_provider,
                get_default_provider, get_default_model_name)
            quest = self.coworker_id
            provider, model_rec = (
                ProviderFactory.from_coworker(quest) if quest else (None, None))
            if not provider:
                # `env` MÅSTE skickas in (samma fälla som eftermälet): utan
                # den faller uppslaget tillbaka på `odoo.http.request`, som
                # inte finns i en bakgrundstråd — och då blir svaret alltid
                # (None, None).
                provider, model_rec = get_default_provider(self.env)
            if not provider:
                _logger.warning(
                    'Name: no provider available for session %s',
                    self.id)
                return None
            model_name = (model_rec and model_rec._get_api_name()) \
                or get_default_model_name()
            # En enda provider-chat, ingen loop: uppgiften är ett anrop utan
            # verktyg. En AgentLoop med max_rounds=1 var en loop som aldrig
            # kunde loopa — den byggde en ToolRegistry som aldrig användes och
            # gick genom samma konstruktionsplats som de riktiga körvägarna
            # (structural-discipline-gates 3.1).
            #
            # 64 räckte inte: en reasoning-modell bränner hela budgeten på
            # sitt tänkande och lämnar inget till svaret. Eftermälet använder
            # 2048; en mening behöver inte mer än så.
            # B7: stäng providern — annars lämnas httpx-tasken pending.
            result = _run_provider(
                provider.chat(
                    model=model_name,
                    messages=[Message(role=Role.USER,
                                      content=self._title_prompt(transcript))],
                    max_tokens=512),
                provider)
            text = (result.text or '').strip()
            # AgentLoop svarar med platshållare när modellen inte gav något
            # ("(no response)", "(max rounds…)", "error: …"). Att sätta ett
            # sådant som sessionsnamn är sämre än att behålla det förra —
            # samma spärr som minnesextraktionen använder.
            from odoo.addons.ai_agent_core.models.ai_coworker import (
                _is_error_answer)
            if _is_error_answer(text):
                _logger.info(
                    'Name: the model gave no usable answer for '
                    'session %s (%r) — keeping the current name',
                    self.id, text[:60])
                return None
            return self._clean_title(text) or None
        except Exception as e:
            _logger.warning(
                'Name: generation failed for session %s: %s',
                self.id, e)
            return None

    @api.model
    def _run_title_in_env(self, dbname, session_id):
        """Kör `_maybe_update_title` i en egen DB-cursor/env (bakgrund).

        Samma mönster som `_run_learn_in_env`: requestens cursor stängs när
        svaret gått ut, så en tråd som lånar den kraschar tyst.
        """
        try:
            from odoo.api import Environment
            from odoo.modules.registry import Registry
            registry = Registry(dbname)
            with registry.cursor() as cr:
                env = Environment(cr, SUPERUSER_ID, {})
                session = env['ai.coworker.session'].browse(session_id)
                if session.exists():
                    session._maybe_update_title()
        except Exception as e:
            _logger.warning('Bakgrundsnamngivning misslyckades: %s', e)

    def _maybe_update_title_async(self):
        """Trigga steg B i bakgrunden. Tyst no-op vid fel."""
        self.ensure_one()
        try:
            import threading
            threading.Thread(
                target=self._run_title_in_env,
                args=(self._cr.dbname, self.id),
                daemon=True,
            ).start()
            return True
        except Exception as e:
            _logger.warning(
                'session %s: kunde inte starta namngivning: %s', self.id, e)
            return False

    # ── Pi-session-markör (transport C) ────────────────────────────────
    # Pi-klienten äger Pi-sessionens UUID. Primär transport är body-fältet
    # `pi_session_id`; som fallback märker klienten system-/första
    # user-meddelandet med `{pi session: <uuid>}`. Denna helper plockar ut
    # UUID:t ur en text så att även klienter/vägar som strippar okända
    # body-fält kan kopplas till rätt session.
    _PI_SESSION_MARKER_RE = re.compile(
        r'\{\s*pi[\s_-]*session\s*:\s*'
        r'([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})'
        r'\s*\}',
        re.IGNORECASE)

    @api.model
    def _extract_pi_session_marker(self, *texts):
        """Plocka ut ett Pi-session-UUID ur `{pi session: <uuid>}`-markörer.

        Tar emot en eller flera texter (system-prompt, user-meddelanden) och
        returnerar det första UUID som hittas, annars ''. Används som
        fallback när request-body saknar `pi_session_id`.
        """
        for text in texts:
            if not text:
                continue
            m = self._PI_SESSION_MARKER_RE.search(str(text))
            if m:
                return m.group(1).lower()
        return ''

    # ── Persistera Pi:s messages[] som session lines ────────────────────
    # Pi (och andra OpenAI-klienter) skickar HELA konversationen i
    # `messages[]` varje anrop. För att varje meddelande ska bli EXAKT en
    # ai.coworker.session.line (och inte dupliceras per anrop) persisteras
    # endast DELTAT: messages[pi_message_count:]. Räknaren på sessionen
    # håller reda på hur många poster som redan sparats.
    #
    # Token-accounting: requestens input-tokens läggs på sista NYA
    # user-raden (prompt-kostnaden), output-tokens på sista NYA
    # assistant-raden. Summan över raderna = anropets (input+output),
    # dvs. samma kostnadssemantik som tidigare.
    @api.model
    def _filter_known_messages(self, env, session, messages,
                               has_tokens=False):
        """Returnera de poster som INTE redan finns i sessionen.

        Används när kursorn är ogiltig (Pi har kompakterat historiken och
        skickar en kortare lista). Jämförelsen görs på content_hash —
        indexerat på (session_id, content_hash) — inte på position.

        Avgörande detalj: en post som redan finns men bär ETT ANNAT
        tokenantal är ett RETRY-ANROP (Pi:s loop skickar samma text igen,
        mätt: 168 anrop med 98 066 / 98 325 / 98 557 tokens 5 s isär). De
        är riktiga anrop som kostade pengar och ska bevaras. Bara poster
        som matchar en befintlig rad exakt — eller som saknar tokens —
        filtreras bort.
        """
        Line = env['ai.coworker.session.line']
        if not messages:
            return []
        # Alla kända (hash → tokenantal) för sessionen, i ett svep.
        rows = Line.search_read(
            [('session_id', '=', session.id),
             ('content_hash', '!=', False)],
            ['content_hash', 'token_input', 'token_output'])
        known = {}
        for r in rows:
            known.setdefault(r['content_hash'], set()).add(
                (r['token_input'] or 0, r['token_output'] or 0))

        def _norm_role(m):
            role = (m.get('role') or '').strip()
            if role == 'developer':
                role = 'system'
            if role not in ('user', 'assistant', 'tool', 'system'):
                role = 'user'
            return role

        def _norm_content(m):
            content = m.get('content')
            if isinstance(content, list):
                content = '\n'.join(
                    c.get('text', '') for c in content
                    if isinstance(c, dict) and c.get('type') == 'text')
            return content or ''

        kept = []
        for m in messages:
            chash = Line._content_hash({
                'role': _norm_role(m),
                'tool_call_id': m.get('tool_call_id') or '',
                'content': _norm_content(m),
            })
            if chash not in known:
                kept.append(m)
            else:
                # Posten finns redan. Är det ett RETRY-ANROP? Pi:s loop
                # skickar samma text igen men bär då ett annat tokenantal.
                # Vi kan inte se anropets tokens här (de bokförs på sista
                # user-raden i loopen nedan), så vi släpper igenom posten
                # när anropet rapporterat tokens — dedupen sker då på
                # (hash, tokenantal) i loopen.
                if has_tokens:
                    kept.append(m)
        return kept

    def _persist_pi_messages(self, env, session, messages,
                             input_t=0, output_t=0, model_real=''):
        """Skriv NYA messages[]-poster som session lines (en per post).

        Returnerar antalet skapade rader. Tyst no-op vid fel (persist får
        aldrig krascha en körning).
        """
        try:
            messages = messages or []
            total = len(messages)
            # Tolerant läsning (1.3): stäm av räknaren mot raderna först —
            # om den pekar förbi antalet rader (t.ex. efter trunkerad
            # historik) sänks den så inget tappas.
            try:
                session._sync_pi_message_count()
            except Exception:
                pass  # best-effort: tyst fallback — se etiketteringsregeln
            already = int(session.pi_message_count or 0)
            # Kursor-antagandet (pi-message-persist-dedup D2): `messages[]`
            # växer monotont. Pi KOMPAKTERAR historiken — då är den nya
            # listan kortare, kursorn pekar förbi, och hela historiken
            # skrevs förr om (mätt: 115 omskrivningar av samma prompt i en
            # session, 224 985 identiska rader i drift).
            #
            # LÖSNING: vid ogiltig kursor skrivs bara de poster som INTE
            # redan finns i sessionen. Jämförelsen görs mot sessionens
            # befintliga rader på content_hash (indexerat på
            # (session_id, content_hash)) — inte mot position.
            cursor_valid = 0 <= already <= total
            if cursor_valid:
                new_messages = messages[already:]
            else:
                new_messages = self._filter_known_messages(
                    env, session, messages,
                    has_tokens=bool(input_t or output_t))
            if not new_messages:
                # Inget nytt — men bokför tokens om anropet ändå kostat.
                if input_t or output_t:
                    session.write({
                        'token_input': (session.token_input or 0) + input_t,
                        'token_output': (session.token_output or 0) + output_t,
                    })
                return 0

            Line = env['ai.coworker.session.line']
            sys_mult = 1.0
            if model_real:
                try:
                    ai_model = env['ai.model']._resolve_from_real(
                        model_real, session.coworker_id)
                    if ai_model:
                        sys_mult = ai_model.sys_multiplier
                except Exception:
                    pass  # best-effort: tyst fallback — se etiketteringsregeln

            # Samma rollnormalisering som meddelandekonverteringen använder
            # (`normalize_role_name`), så de två vägarna inte kan glida isär.
            from odoo.addons.ai_agent_core.core.provider import (
                normalize_role_name)

            # Index för sista nya user-/assistant-raden (token-bokföring).
            def _norm_role(m):
                # Pi:s interna vokabulär: `toolResult` är en verktygsrad, inte
                # en användartur (session-observability). Utan den här
                # mappningen föll resultaten tillbaka på `user` nedan, och
                # sessionen visade verktygets utdata som om användaren skrivit
                # det — medan `tool_call_id` tappades.
                role = normalize_role_name(m.get('role') or '')
                if role == 'developer':
                    role = 'system'
                if role not in ('user', 'assistant', 'tool', 'system'):
                    role = 'user'
                return role

            def _norm_content(m):
                content = m.get('content')
                if isinstance(content, list):
                    content = '\n'.join(
                        c.get('text', '') for c in content
                        if isinstance(c, dict) and c.get('type') == 'text')
                return content or ''

            last_user_idx = -1
            last_asst_idx = -1
            for i, m in enumerate(new_messages):
                role = _norm_role(m)
                if role == 'user':
                    last_user_idx = i
                elif role == 'assistant':
                    last_asst_idx = i

            # Kända hashar/tokenantal för sessionen — underlag för dedupen.
            #
            # Bara vid OGILTIG kursor (pi-message-persist-dedup 2.3): då är
            # deltat inte pålitligt och varje post måste jämföras mot det som
            # redan finns. Vid en giltig kursor är deltat per definition nytt,
            # och att läsa sessionens alla rader för att bygga kartan skulle
            # kosta en genomgång av 30 000+ rader för fem nya poster. Den
            # giltiga kursorn är snabbvägen — inga hash-uppslagningar.
            known_hashes = set()
            known_tokens = {}
            if not cursor_valid:
                for r in env['ai.coworker.session.line'].search_read(
                        [('session_id', '=', session.id),
                         ('content_hash', '!=', False)],
                        ['content_hash', 'token_input', 'token_output']):
                    known_hashes.add(r['content_hash'])
                    known_tokens.setdefault(r['content_hash'], set()).add(
                        (r['token_input'] or 0, r['token_output'] or 0))

            base_seq = 0
            if already:
                max_rec = env['ai.coworker.session.line'].search(
                    [('session_id', '=', session.id)],
                    order='sequence desc, id desc', limit=1)
                base_seq = (max_rec.sequence or 0) + 1
            # Dedup mot sista persisterade raden: svaret från FÖRRA anropet
            # skrivs direkt (assistant-rad) och dyker sedan upp som första
            # post i nästa anrops delta. Utan dedup skulle det dubbleras.
            last_line = env['ai.coworker.session.line'].search(
                [('session_id', '=', session.id)],
                order='sequence desc, id desc', limit=1)
            created_count = 0
            for i, m in enumerate(new_messages):
                role = _norm_role(m)
                content = _norm_content(m)

                # Hoppa över om detta är exakt samma som sista raden (svaret
                # som redan skrivits direkt vid föregående anrop).
                #
                # BARA när posten saknar tokens: den direktskrivna raden bär
                # inga, medan ett retry-anrop av samma text bär ett annat
                # tokenantal och är ett eget, betalt anrop (pi-message-
                # persist-dedup D3). Utan token-villkoret svaldes retryn här
                # innan (hash, token)-dedupen längre ner ens fick se dem.
                #
                # `tool_call_id` måste också stämma: två tool-poster med samma
                # text men olika id är två anrop (D5).
                if (last_line and i == 0 and last_line.role == role
                        and (last_line.content or '') == content
                        and not (input_t or output_t)
                        and (last_line.tool_call_id or '')
                        == (m.get('tool_call_id') or '')):
                    last_line = env['ai.coworker.session.line'].browse(0)
                    continue

                vals = {
                    'session_id': session.id,
                    'role': role,
                    'content': content,
                    'sequence': base_seq + i,
                    'sys_multiplier': sys_mult,
                    'model_real': model_real or '',
                }
                # Tool-meddelanden: OpenAI:s tool_call_id paras med
                # assistant-radens tool_calls[].id (pi-session-handover).
                #
                # VARFÖR båda fälten: `tool_call_id` är den EXAKTA
                # parningen — utan den måste en läsare gissa ihop anrop
                # och resultat via tool_name + ordning, vilket går sönder
                # så snart två anrop av samma verktyg följer på varandra.
                # `tool_name` är för läsbarhet och för ai.tool:s
                # meddelandelista. Fram till 2026-10-14 sparades bara
                # namnet, och id:t slängdes — kommentaren sa till och med
                # "spara som namn".
                if role == 'tool':
                    # Pi:s egen post bär `toolCallId`/`toolName` (camelCase);
                    # OpenAI-formatet bär `tool_call_id`/`name`. Läs båda —
                    # annars tappas id:t på Pi-vägen och verktygsraden kan
                    # inte paras med assistant-radens `tool_calls[].id`
                    # (session-observability).
                    vals['tool_name'] = (m.get('name')
                                         or m.get('toolName') or '')
                    vals['tool_call_id'] = (m.get('tool_call_id')
                                            or m.get('toolCallId') or '')
                if m.get('tool_calls'):
                    vals['tool_calls'] = json.dumps(
                        m.get('tool_calls'), ensure_ascii=False)
                # Token-bokföring (endast sista nya user/assistant).
                if i == last_user_idx and input_t:
                    vals['token_input'] = input_t
                if i == last_asst_idx and output_t:
                    vals['token_output'] = output_t
                # Dedup på (hash, tokenantal) — pi-message-persist-dedup D1/D3.
                # Samma post utan tokens = dubblett. Samma post MED tokens som
                # skiljer sig från den befintliga radens = retry-anrop, och
                # bevaras (mätt: 168 anrop med 98 066 / 98 325 / 98 557).
                chash = Line._content_hash(vals)
                vals['content_hash'] = chash
                new_tokens = (vals.get('token_input') or 0,
                              vals.get('token_output') or 0)
                if new_tokens == (0, 0) and chash in known_hashes:
                    continue
                if new_tokens != (0, 0) and new_tokens in known_tokens.get(
                        chash, set()):
                    continue
                Line.create(vals)
                known_hashes.add(chash)
                known_tokens.setdefault(chash, set()).add(new_tokens)
                created_count += 1

            # Kursor: bara ett giltigt monotont värde får sparas. Vid
            # kompaktering är listan kortare — att spara `total` då skulle
            # göra nästa anrops delta fel, så kursorn lämnas orörd och
            # hash-dedupen bär sanningen (D2).
            cursor_vals = {}
            if cursor_valid:
                cursor_vals['pi_message_count'] = total
            session.write(dict(
                cursor_vals,
                token_input=(session.token_input or 0) + input_t,
                token_output=(session.token_output or 0) + output_t,
            ))
            # Räknaren är ett derivat av raderna (1.3): synka mot faktiskt
            # antal rader så den aldrig driver iväg. Loggar avvikelse utan
            # att fälla körningen.
            try:
                session._sync_pi_message_count()
            except Exception:
                pass  # best-effort: tyst fallback — se etiketteringsregeln
            return created_count
        except Exception as e:
            _logger.warning('pi message persist failed: %s', e)
            return 0

    create_date = fields.Datetime('Started', default=lambda self: fields.Datetime.now())
    end_date = fields.Datetime('Ended')
    round_count = fields.Integer('Rounds', default=0)
    # Typat utfall (utfall-och-tokenmatning D3): Char → Selection. Fritext
    # (t.ex. `str(e)[:200]`) flyttar till `error_detail` — annars förstörs
    # räknebarheten. `length` skiljs från `max_rounds`: modellens egen gräns
    # vs vår loop som ger upp (olika åtgärder hjälper).
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
        ('setup_failed', 'Setup Failed'),
        ('idle', 'Idle'),
        ('closed', 'Closed'),
        ('interrupted', 'Interrupted'),
        ('new_session', 'New Session'),
    ], string='Finish Reason')

    # Bifrost-berikningen (kostnad, retries, fallbacks) ägs av `ai_agent_core`
    # som SKALÄRA fält — de nämner ingen brygga. Anropslistan
    # (`bifrost_request_log_ids`) ligger i `bifrost` (computed), för kärnan får
    # inte namnge bryggan: `bifrost.request.log` finns inte i en installation
    # utan bifrost, och en One2many dit kraschade registry-laddningen
    # (KeyError: 'session_ref_id', Failed to load registry).
    #
    # SEPARATA mått: slås aldrig samman med `token_sys` (debitering) eller
    # `cost_usd` (Odoos egen kostnad) — gatewayen mäter något annat.
    # `bifrost_cost` är omätt (False) tills en cron sett gateway-rader.
    bifrost_cost = fields.Float(
        'Bifrost-kostnad (USD)',
        help='Sum of gateway cost for the session calls. Unmeasured = False.',
    )
    bifrost_retries = fields.Integer(
        'Bifrost-retries',
        help='Sum of number_of_retries across the session gateway calls.',
    )
    bifrost_fallbacks = fields.Integer(
        'Bifrost-fallbacks',
        help='Number of calls where the gateway switched provider '
             '(fallback_index > 1).',
    )
    bifrost_synced_at = fields.Datetime(
        'Bifrost synkad',
        help='When the gateway log was last aggregated.',
    )

    user_id = fields.Many2one('res.users', default=lambda self: self.env.user,
        index=True)
    company_id = fields.Many2one('res.company', default=lambda self: self.env.company)

    # Mail-svar med dröjsmål (mail-trigger): svarstexten postas av cron
    # när reply_at passerats.
    pending_reply = fields.Text('Pendande mail-svar',
        help='Reply text posted by cron after the reply delay.')
    reply_at = fields.Datetime('Svara efter',
        help='When the delayed reply should be posted.')

    # Thread support — `name` är enda trådnamnet (thread_name borttaget).
    memory_ids = fields.One2many('ai.memory', 'session_id', string='Session Memories',
        help='Uploaded documents and FAISS memories for this session')
    session_line_ids = fields.One2many(
        'ai.coworker.session.line', 'session_id', string='Messages')
    line_count = fields.Integer('Messages', compute='_compute_line_count')
    attachment_ids = fields.One2many(
        'ir.attachment', compute='_compute_attachment_ids',
        string='Bilagor', store=False)
    attachment_count = fields.Integer(
        'Bilagor', compute='_compute_attachment_ids')
    hitl_ids = fields.One2many(
        'ai.coworker.hitl', 'session_id', string='HITL-requests')
    hitl_open_count = fields.Integer(
        'Open HITL', compute='_compute_hitl_open_count')
    active = fields.Boolean('Active', default=True)

    @api.depends('hitl_ids.state')
    def _compute_hitl_open_count(self):
        for r in self:
            r.hitl_open_count = len(
                r.hitl_ids.filtered(lambda h: h.state == 'asked'))

    @api.depends('session_line_ids')
    def _compute_line_count(self):
        for r in self:
            r.line_count = len(r.session_line_ids)

    def _compute_attachment_ids(self):
        for r in self:
            r.attachment_ids = self.env['ir.attachment'].search([
                ('res_model', '=', 'ai.coworker.session'),
                ('res_id', '=', r.id),
            ])
            r.attachment_count = len(r.attachment_ids)

    def action_open_attachments(self):
        """Öppna sessionens bilagor (ir.attachment)."""
        return {
            'name': 'Bilagor',
            'type': 'ir.actions.act_window',
            'res_model': 'ir.attachment',
            'view_mode': 'list,form',
            'views': [[False, 'list'], [False, 'form']],
            'target': 'current',
            'domain': [('res_model', '=', 'ai.coworker.session'),
                       ('res_id', '=', self.id)],
        }

    # ── Spår-knappar (session-observability) ────────────────────────────

    def action_open_agent_runs(self):
        """Öppna sessionens agentkörningar."""
        self.ensure_one()
        return {
            'name': _('Agent Runs'),
            'type': 'ir.actions.act_window',
            'res_model': 'ai.coworker.session.agent_run',
            'view_mode': 'list,form',
            'domain': [('session_id', '=', self.id)],
            'context': {'default_session_id': self.id},
        }

    def action_open_tool_calls(self):
        """Öppna sessionens verktygsanrop."""
        self.ensure_one()
        return {
            'name': _('Tool Calls'),
            'type': 'ir.actions.act_window',
            'res_model': 'ai.coworker.session.tool_call',
            'view_mode': 'list,form',
            'domain': [('session_id', '=', self.id)],
            'context': {'default_session_id': self.id},
        }

    def action_open_close_events(self):
        """Öppna sessionens stängningshistorik."""
        self.ensure_one()
        return {
            'name': _('Close Events'),
            'type': 'ir.actions.act_window',
            'res_model': 'ai.coworker.session.close_event',
            'view_mode': 'list',
            'domain': [('session_id', '=', self.id)],
        }

    def action_open_hitl(self):
        """Öppna sessionens HITL-requests (godkännanden)."""
        self.ensure_one()
        return {
            'name': 'HITL-requests',
            'type': 'ir.actions.act_window',
            'res_model': 'ai.coworker.hitl',
            'view_mode': 'list,form',
            'views': [[False, 'list'], [False, 'form']],
            'target': 'current',
            'domain': [('session_id', '=', self.id)],
        }

    def action_get_lines(self):
        return {
            'name': 'Messages', 'type': 'ir.actions.act_window',
            'res_model': 'ai.coworker.session.line', 'view_mode': 'list,form',
            'views': [[False, 'list'], [False, 'form']],
            'target': 'current',
            'domain': [('session_id', '=', self.id)],
            'context': {'default_session_id': self.id},
        }

    #: Utfall där sessionen är värd att befordras till ett regressionstest.
    #: `stop`/`tool_calls` saknas med flit — en lyckad session är inte ett
    #: testfall, och knappen ska inte erbjuda det.
    PROMOTABLE_FINISH_REASONS = ('error', 'max_rounds', 'length', 'timeout')

    def action_promote_to_eval_case(self):
        """Befordra sessionen till ett `ai.eval.case` (HITL).

        Ändring: wire-orphaned-core-mechanisms 4.1/4.2.

        Människan trycker på knappen — inget automatiskt anrop finns (4.4).
        Testfallets `input` är sessionens första `user`-rad: det som
        faktiskt efterfrågades, inte en sammanfattning av det. Förväntan
        (`expected_contains`) lämnas TOM — den måste fyllas i av en
        människa, annars poängsätter testet bara "gav samma svar som förra
        gången".

        Returns:
            dict: action som öppnar det nya testfallet.

        Raises:
            ValidationError: om sessionen saknar en `user`-rad, redan är
                befordrad, eller saknar medarbetare.
        """
        self.ensure_one()
        if not self.coworker_id:
            raise ValidationError(_(
                'Sessionen saknar medarbetare — den kan inte bli ett '
                'testfall.'))

        existing = self.env['ai.eval.case'].search(
            [('source_session_id', '=', self.id)], limit=1)
        if existing:
            raise ValidationError(_(
                'The session is already promoted to test case "%(case)s".',
                case=existing.display_name or existing.id))

        first_user_line = self.session_line_ids.filtered(
            lambda line: line.role == 'user').sorted('sequence')[:1]
        if not first_user_line:
            raise ValidationError(_(
                'The session has no user line — there is no prompt to '
                'promote. A session without a question cannot become a test '
                'case.'))

        case = self.env['ai.eval.case'].create({
            'coworker_id': self.coworker_id.id,
            'source_session_id': self.id,
            'input': first_user_line.content or '',
            'category': self.finish_reason or 'general',
        })
        _logger.info(
            'Session %s befordrad till eval-fall %s (finish_reason=%s)',
            self.id, case.id, self.finish_reason or '-')
        return {
            'name': _('Evaluation Case'),
            'type': 'ir.actions.act_window',
            'res_model': 'ai.eval.case',
            'res_id': case.id,
            'view_mode': 'form',
            'views': [[False, 'form']],
            'target': 'current',
        }

    def save_config(self, config: dict):
        self.config_json = json.dumps(config, default=str)

    def add_tokens(self, input_t: int, output_t: int, model_real: str = ''):
        """Record token usage and create a session line with systemtoken tracking."""
        self.token_input += input_t
        self.token_output += output_t

        # Look up sys_multiplier from ai.model (kanal-medvetet)
        sys_mult = 1.0
        if model_real:
            ai_model = self.env['ai.model']._resolve_from_real(
                model_real, self.coworker_id)
            if ai_model:
                sys_mult = ai_model.sys_multiplier

        # Create session line for token tracking
        self.env['ai.coworker.session.line'].create({
            'session_id': self.id,
            'role': 'assistant',
            'content': f'Tokens: {input_t} in / {output_t} out',
            'token_input': input_t,
            'token_output': output_t,
            'model_real': model_real,
            'sys_multiplier': sys_mult,
        })

    # ── Sessionens eftermäle (D4) ────────────────────────────────
    #
    # EN skrivare till `summary`. Tidigare fanns fyra
    # sammanfattningsvägar varav bara en (buzz-vägen) skrev fältet —
    # och bara i buzz-läge. Resultatet var att en vanlig chatt fick
    # inget eftermäle alls, och konsolideringen läste en råsvans av
    # de sista 40 raderna i stället.

    MIN_SUMMARY_LINES = 4

    SUMMARY_HEADINGS = (
        'Syfte', 'Utfall', 'Beslut', 'Fakta', 'Artefakter',
        'Open questions',
    )

    def _final_summary_prompt(self, transcript):
        """Strukturerad, svensk prompt (D4 / session-close krav 5).

        Rubrikerna är fasta eftersom eftermälet är konsolideringens
        råvara: samma form varje gång gör den jämförbar och sökbar med
        svensk fulltextsökning.
        """
        headings = '\n'.join('### %s' % h for h in self.SUMMARY_HEADINGS)
        return (
            'Summarise the session below in Swedish. Use exactly these headings '
            'and no others:\n\n%s\n\n'
            'Omit a heading entirely if the section has no content — never '
            'write invented content to fill it out. Be concise but complete: '
            'facts, decisions and open questions are what matter.\n\n'
            '--- CONVERSATION ---\n%s' % (headings, transcript))

    def _final_summary_transcript(self, lines, max_chars=12000):
        """Bygg konversationsunderlaget för eftermälet.

        Till skillnad från `_learn_from_session` (som bara såg de sista
        40 raderna) täcker detta hela sessionen, trunkerad bakifrån så att
        slutet — där utfallet finns — alltid kommer med.
        """
        parts = []
        for ln in lines:
            if not ln.content:
                continue
            parts.append('[%s] %s' % (ln.role, ln.content[:500]))
        # Utfallet som kontext (utfall-och-tokenmatning D6/6.1): LLM:en kan då
        # skriva VARFÖR sessionen slutade. Problemen lagras inte i `summary`
        # som data — de bor i typade fält och kaizen. Summary är berättelsen;
        # fälten är räkningen.
        if self.finish_reason and self.finish_reason != 'stop':
            parts.append(
                '[outcome] finish_reason=%s status=%s'
                % (self.finish_reason, self.status or ''))
        text = '\n'.join(parts)
        if len(text) > max_chars:
            text = text[-max_chars:]
        return text

    def _write_final_summary(self, force=False):
        """Skriv sessionens eftermäle — idempotent (D4).

        Returnerar sammanfattningen (str) eller None. Anropas vid
        stängning (`mark_done`/`mark_interrupted`) och av idle-cronen.

        Idempotensen hänger på `summary_message_count`: har inga nya rader
        tillkommit sedan förra sammanfattningen görs INGET LLM-anrop. Det
        är det som gör att stängning och cron kan köra samtidigt utan att
        kosta dubbla anrop — och utan att skriva om oförändrat innehåll.
        """
        self.ensure_one()
        lines = self.session_line_ids.sorted('sequence')
        total = len(lines)

        # 1. Idempotens — inget nytt sedan sist → inget LLM-anrop.
        if not force and self.summary and \
                self.summary_message_count == total:
            _logger.debug(
                'Aftermath: session %s unchanged (%d lines) — skipping',
                self.id, total)
            return self.summary

        # 2. För kort session → ingen tom sammanfattning.
        if total < self.MIN_SUMMARY_LINES:
            _logger.debug(
                'Aftermath: session %s too short (%d < %d lines) — '
                'no summary',
                self.id, total, self.MIN_SUMMARY_LINES)
            return None

        transcript = self._final_summary_transcript(lines)
        if not transcript.strip():
            _logger.debug(
                'Aftermath: session %s has no content — skipping',
                self.id)
            return None

        summary = self._run_final_summary_llm(transcript)
        if not summary:
            _logger.warning(
                'Aftermath: LLM summary failed for session %s '
                '(%d lines) — summary_message_count left untouched so the '
                'next attempt retries', self.id, total)
            return None

        self.sudo().write({
            'summary': summary,
            'summary_message_count': total,
        })
        _logger.info(
            'Aftermath written for session %s (%d lines, %d characters)',
            self.id, total, len(summary))

        # Steg C (session-title A→B→C): eftermälet är den sista och bästa
        # källan till ett namn — det finns först här, men det beskriver
        # HELA sessionen. `_maybe_set_title` rangordnar källorna, så ett
        # manuellt satt namn rörs aldrig.
        #
        # VARFÖR en utplockad mening och inte hela eftermälet
        # (chat-session-limits-and-names D3): eftermälet börjar alltid med
        # en rubrik (`## Syfte`). `_clean_title` avvisar numera rubrikord, så
        # att skicka hela texten hade alltid gett tomt. Att plocka ut första
        # meningsfulla meningen bevarar källans värde.
        self._maybe_set_title(
            self._title_from_summary(summary), source='summary')

        # Bron till det personliga minnet (session-memory-bridge D2).
        # Eftermälet är den naturliga platsen: här finns både transcriptet
        # och vetskapen att sessionen är slut. Anropet är idempotent.
        #
        # SAVEPOINT är obligatorisk, inte kosmetik. Bron gör LLM- och
        # SQL-anrop; kastar något av dem utan savepoint förgiftas hela
        # transaktionen (InFailedSqlTransaction) och ALLT efterföljande
        # dör — inklusive `session.write({'status': 'done'})` i
        # _cron_close_idle_sessions, som då aldrig stängde sessionen.
        # Det var mekanismen bakom idle-cronens failure_count: eftermälet
        # skrevs, men stängningen kraschade, och samma session plockades
        # upp igen var 15:e minut i evighet.
        #
        # Ett anrop som FÅR misslyckas måste ha en savepoint — try/except
        # räcker inte, transaktionen är förgiftad ändå.
        try:
            with self.env.cr.savepoint():
                self._bridge_to_personal_memory()
        except Exception:
            _logger.exception(
                'Aftermath: the memory bridge failed for session %s — '
                'the aftermath is written and the session closes anyway',
                self.id)
        return summary

    def _bridge_to_personal_memory(self):
        """Skriv sessionens erfarenhet vidare till personligt minne.

        `extract_from_session()` var byggd men hade NOLL anropare —
        erfarenhet blev aldrig minne. Här kopplas den in.

        Tre skyddsnät:

        1. **Idempotens** — `memory_extracted` sätts när extraktionen kört.
           Eftermälet kan anropas från flera håll (mark_done, idle-cron,
           buzz) och får inte ge dubbla LLM-anrop.
        2. **Rätt användare** — upplösningen går via
           `_resolve_dispatch_user()`, inte `session.user_id` rakt av.
           En cron-session har `user_id = systemuser`, och att skriva
           personligt minne till systemuser vore både fel och otillåtet.
        3. **Tröskel** — bara sessioner som nådde `MIN_SUMMARY_LINES`
           bär tillräcklig erfarenhet. Kortare sessioner hoppas över.

        Returns:
            int: Antal extraherade minnen (0 om inget gjordes).
        """
        self.ensure_one()

        # 1. Idempotens
        if self.memory_extracted:
            _logger.debug(
                'Memory: session %s already extracted — skipping', self.id)
            return 0

        # 2. Tröskel — samma som eftermälet
        total = len(self.session_line_ids)
        if total < self.MIN_SUMMARY_LINES:
            _logger.debug(
                'Memory: session %s too short (%d < %d lines) — no '
                'extraction', self.id, total, self.MIN_SUMMARY_LINES)
            return 0

        coworker = self.coworker_id
        if not coworker:
            return 0

        # 3. Rätt användare — aldrig systemuser
        #
        # `_resolve_dispatch_user` är byggd för dispatch FÖRE en körning,
        # där `env.uid` är den som tryckte. Här är vi EFTER körningen, i
        # en cron-process — då är `env.user` systemuser och upplösningen
        # faller. Vi ger därför sessionens egen `user_id` som sista utväg:
        # den är satt vid skapandet och är den som faktiskt ägde körningen.
        user = None
        try:
            user = coworker._resolve_dispatch_user(
                init_type=self.init_type or None, session=self)
        except Exception as e:
            _logger.debug(
                'Memory: session %s — dispatch resolution failed (%s), '
                'falling back to the session user_id', self.id, e)

        if not user and self.user_id:
            user = self.user_id

        if not user:
            _logger.warning(
                'Memory: session %s has no user — no extraction',
                self.id)
            return 0

        if user == self.env.ref('base.user_root'):
            _logger.warning(
                'Memory: session %s resolved to the system user — no '
                'extraction (personal memory requires a real user)',
                self.id)
            return 0

        try:
            count = self.env['ai.personal.memory'].sudo().extract_from_session(
                self.id)
        except Exception as e:
            _logger.error(
                'Memory: extraction from session %s failed: %s',
                self.id, e, exc_info=True)
            return 0

        # Markera ÄVEN vid 0 extraherade — annars kör varje eftermäle om
        # samma LLM-anrop för en session som inte gav något.
        self.sudo().write({'memory_extracted': True})
        _logger.info(
            'Memory: session %s extracted %d memories for user %s',
            self.id, count, user.login)
        return count

    def _run_final_summary_llm(self, transcript):
        """Kör LLM-anropet för eftermälet. Returnerar str eller None.

        Avskilt från `_write_final_summary` så att idempotens-logiken kan
        testas utan att röra providern.
        """
        import asyncio
        try:
            from odoo.addons.ai_agent_core.core.provider import (
                Message, ProviderFactory, Role, _run_provider,
                get_default_provider, get_default_model_name)
            quest = self.coworker_id
            provider, model_rec = (
                ProviderFactory.from_coworker(quest) if quest else (None, None))
            if not provider:
                # `env` MÅSTE skickas in. Utan den faller funktionen
                # tillbaka på `odoo.http.request`, som inte finns i cron
                # — och då blir svaret alltid (None, None). Det var
                # därför varje API-session (coworker_id = false) fick
                # "ingen provider tillgänglig" och aldrig något eftermäle.
                # Samma fälla som _resolve_dispatch_user nedan beskriver.
                provider, model_rec = get_default_provider(self.env)
            if not provider:
                _logger.warning(
                    'Aftermath: no provider available for session %s',
                    self.id)
                return None
            model_name = (model_rec and model_rec._get_api_name()) \
                or get_default_model_name()
            # En enda provider-chat, ingen loop — se _run_title_llm ovan.
            # B7: stäng providern — annars lämnas httpx-tasken pending.
            result = _run_provider(
                provider.chat(
                    model=model_name,
                    messages=[Message(role=Role.USER,
                                      content=self._final_summary_prompt(transcript))],
                    max_tokens=2048),
                provider)
            return (result.text or '').strip()[:4000] or None
        except Exception as e:
            _logger.warning(
                'Aftermath: summary failed for session %s: %s',
                self.id, e)
            return None

    @api.model
    def _cron_close_idle_sessions(self, idle_minutes=None, batch_size=20,
                                  extra_domain=None):
        """Stäng övergivna sessioner och skriv deras eftermäle (D4, 4.4).

        Varför denna cron: `mark_done()` anropas bara från webhook-vägen
        och `mark_interrupted()` aldrig i drift. Majoriteten av alla
        sessioner stängs därför aldrig — de bara slutar få rader. Utan
        cronen får de inget eftermäle, och konsolideringen har inget att
        läsa.

        Idempotent: `_write_final_summary` gör inget LLM-anrop när inga
        nya rader tillkommit, och en stängd session plockas inte upp igen
        (status-filtret).

        `extra_domain` används av tester för att begränsa batchen — i
        drift finns det alltid äldre sessioner som annars fyller batchen.
        """
        if idle_minutes is None:
            idle_minutes = int(
                self.env['ir.config_parameter'].sudo().get_param(
                    'ai_agent_core.session_idle_minutes', '60') or 60)
        cutoff = fields.Datetime.now() - timedelta(minutes=idle_minutes)
        domain = [
            ('status', '=', 'active'),
            ('write_date', '<', cutoff),
        ] + list(extra_domain or [])
        sessions = self.search(domain, limit=batch_size, order='write_date asc')
        if not sessions:
            return 0

        closed = 0
        empty = 0
        for session in sessions:
            try:
                # Upptäck tomma sessioner (session-memory-bridge D4).
                #
                # En session utan rader är ett spår av ingenting — den
                # skapades men kördes aldrig. Den ska STÄNGAS (annars
                # svälter den ut kön), men den ska inte stängas TYST:
                # 61 sådana hittades 2026-09-15, och ingenstans gick det
                # att se varför. Loggen är det enda som skiljer en bugg
                # från en tom konversation i statistiken.
                if not session.session_line_ids:
                    empty += 1
                    _logger.warning(
                        'Session %s is closing EMPTY (0 lines) — coworker=%s '
                        'init_type=%s created=%s. A session should carry a '
                        'run; this one did not.',
                        session.id,
                        session.coworker_id.name or '-',
                        session.init_type or '(tom)',
                        session.create_date)

                session._write_final_summary()
                # 'done' (inte 'interrupted'): sessionen är inte avbruten,
                # den är färdigpratad. Distinktionen spelar roll för
                # resumable-logiken.
                #
                # OBS: stängningen sker i ett write() EFTER sammanfattningen.
                # Misslyckas sammanfattningen ändå stängs sessionen — annars
                # skulle en session som aldrig kan sammanfattas (t.ex. för
                # kort) bli liggande i kön för evigt och svälta ut alla
                # nyare sessioner ur batchen.
                _now = fields.Datetime.now()
                # Stängningen blir en händelse i historiken, inte bara ett
                # tillstånd (session-observability). Utan raden skulle en
                # cron-stängning se ut som en klientstängning i statistiken
                # — och "hur ofta överges en session?" gick inte att svara på.
                try:
                    CloseEvent = self.env[
                        'ai.coworker.session.close_event'].sudo()
                    # Redan registrerad (t.ex. klienten hann före, eller en
                    # tidigare cron-körning i samma sekund) → ingen ny rad.
                    # Sökningen är idempotensen nu när UNIQUE togs bort.
                    if not CloseEvent.search([
                            ('session_id', '=', session.id),
                            ('reason', '=', 'idle'),
                            ('at', '=', _now)], limit=1):
                        CloseEvent.create({
                            'session_id': session.id,
                            'reason': 'idle',
                            'at': _now,
                            'pi_session_id': session.pi_session_id or False,
                        })
                except Exception:
                    # Redan registrerad (t.ex. klienten hann före) — inte ett
                    # fel. Spåret får aldrig hindra stängningen.
                    _logger.debug(
                        'idle cron: close event already recorded for '
                        'session %s', session.id)
                session.sudo().write({
                    'status': 'done',
                    'finish_reason': 'idle',
                    'end_date': _now,
                })
                closed += 1
            except Exception:
                _logger.exception(
                    'Idle cron: could not close session %s', session.id)
        if closed:
            _logger.info(
                'Idle cron: closed %d sessions without activity for %d min '
                '(%d of them were empty)',
                closed, idle_minutes, empty)
        return closed

    def mark_done(self, reason='stop'):
        for session in self:
            try:
                session._write_final_summary()
            except Exception:
                # Eftermälet får aldrig hindra stängningen.
                _logger.exception(
                    'Aftermath failed at mark_done for session %s',
                    session.id)
        self.status = 'done'
        self.finish_reason = reason
        self.end_date = fields.Datetime.now()
        self._push_done_notification()

    # ── Handover (pi-session-handover) ────────────────────────────────
    #
    # Handover sker alltid INOM SAMMA ANVÄNDARE: samma person fortsätter
    # sitt eget arbete på en annan maskin. Att lämna över till en annan
    # person är delegering, inte handover.
    #
    # Listan är användarens EGEN — det är en behörighetsgräns, inte ett
    # UI-filter. Sessioner bär konversationer, projektkontext och
    # kundkoppling; att lista en kollegas avbrutna arbete vore ett
    # dataläckage. Se _handover_owned_domain().

    #: Init-typer som är AUTOMATISKA (ingen människa bad om körningen).
    #: De kan se avbrutna ut men är inte någons ofärdiga arbete.
    HANDOVER_AUTO_INIT_TYPES = (
        'cron', 'watch', 'webhook', 'mail', 'heartbeat',
    )

    def _handover_owned_domain(self, user=None):
        """Domän som begränsar handover till den aktuella användaren.

        Behörighetsgränsen ligger här (och inte bara i ett vy-filter) så
        att ett direkt anrop till listmetoden inte kan läsa någon annans
        sessioner.
        """
        user = user or self.env.user
        return [('user_id', '=', user.id)]

    def action_handover_offer(self, note='', mode='note'):
        """Begär en explicit handover (D3).

        Ett LÖFTE, inte en timeout: tillståndet gäller tills sessionen
        claimas och startas igen — det kan ligga i dagar eller dygn, och
        städas aldrig av en cron.
        """
        self.ensure_one()
        self.write({
            'handover_state': 'offered',
            'handover_note': note or '',
            'handover_mode': mode or 'note',
            'handover_at': fields.Datetime.now(),
            'handover_by': self.env.user.id,
        })
        return True

    def action_handover_claim(self, pi_session_id=''):
        """Ta över en handover från en annan Pi (D1/D2/D5).

        Handover fortsätter i SAMMA Odoo-session — ett medvetet undantag
        från fork-regeln. `pi_session_id` blir den nya innehavaren;
        `pi_session_ids` bär historiken (append-only).

        Ägarskapet överförs atomiskt via mesh:ens NATS KV-lås (D5). Kan
        låset inte tas skrivs INGET — en misslyckad claim får aldrig
        lämna en felaktig pekare.
        """
        self.ensure_one()
        if self.handover_state not in ('offered', 'claimed'):
            from odoo.exceptions import UserError
            raise UserError(
                'The session has no pending handover to claim '
                '(state=%s).' % self.handover_state)
        if self.handover_by and self.handover_by != self.env.user:
            from odoo.exceptions import UserError
            raise UserError(
                'Handover happens within the same user. This session '
                'belongs to %s.' % self.handover_by.name)

        # Atomiskt lås FÖRE varje skrivning (D5). Misslyckas låset
        # returnerar vi False utan att ha rört sessionen.
        if not self._handover_take_lock(pi_session_id):
            return False

        history = [s for s in (self.pi_session_ids or '').split(',') if s]
        if pi_session_id and pi_session_id not in history:
            history.append(pi_session_id)
        self.write({
            'handover_state': 'claimed',
            'handover_claimed_by': self.env.user.id,
            'handover_claimed_at': fields.Datetime.now(),
            'pi_session_id': pi_session_id or self.pi_session_id,
            'pi_session_ids': ','.join(history),
        })
        return True

    def action_handover_done(self):
        """Infria löftet — sessionen lämnar listan över hängande."""
        self.ensure_one()
        self.write({'handover_state': 'done'})
        self._handover_release_lock()
        return True

    def _handover_take_lock(self, pi_session_id=''):
        """Ta sessionslåset i mesh:ens NATS KV-bucket (D5).

        Compare-and-swap: `create()` lyckas bara om nyckeln inte finns.
        Den som först skriver vinner; den andra backar.

        Best-effort: är NATS otillgängligt kan ingen claim göras — men
        det är bättre att neka än att tillåta två ägare.

        Bucketen är samma som resurslåsen (`pi_mesh_locks`, se
        `nats.ts` i Pi-extensionen).
        """
        import json
        key = 'session.' + _handover_kv_key(self.pi_session_id or str(self.id))
        payload = {
            'session_id': self.id,
            'owner': pi_session_id or str(self.env.user.id),
            'at': fields.Datetime.now().isoformat(),
        }
        try:
            from ..core.tools import nats_kv_create
            return nats_kv_create(key, json.dumps(payload))
        except Exception:
            _logger.exception(
                'Handover: could not take the session lock for session %s',
                self.id)
            return False

    def _handover_release_lock(self):
        """Släpp sessionslåset (vid done). Best-effort."""
        key = 'session.' + _handover_kv_key(self.pi_session_id or str(self.id))
        try:
            from ..core.tools import nats_kv_delete
            nats_kv_delete(key)
        except Exception:
            _logger.debug(
                'Handover: could not release the lock for session %s', self.id)

    def _handover_derive_note(self):
        """Härled en handover-anteckning ur sessionens eftermäle (--auto).

        Eftermälet har redan de rubriker en handoff behöver (Syfte, Utfall,
        Beslut, Fakta, Artefakter, Öppna frågor) — det är samma text som
        Pi Durable:s reset(handoff) vill ha. Vi behöver inte bygga
        anteckningsgenerering; vi behöver peka på den som redan finns.
        """
        self.ensure_one()
        if self.summary:
            return self.summary.strip()
        # Ingen sammanfattning än — skriv en (idempotent, inget LLM-anrop
        # om inga nya rader tillkommit).
        try:
            self._write_final_summary()
        except Exception:
            _logger.exception(
                'Handover: could not write the aftermath for session %s',
                self.id)
        return (self.summary or '').strip()

    def _handover_suggest_mode(self):
        """Föreslå handover-läge utifrån hur arbetet såg ut (D6).

        Många verktygsanrop i svansen → `full` (verktygsspåret är beviset,
        nödvändigt när arbetet är "mitt-i-handen"). Beslut i slutet och få
        verktyg → `note` (friskt sammanhang, litet kontextfönster).

        Människan kan alltid överrida — det är den "fyll på med
        information" som det planerade fallet handlar om.
        """
        self.ensure_one()
        lines = self.session_line_ids.sorted('sequence')
        if not lines:
            return 'note'
        tail = lines[-10:]
        tool_count = len([l for l in tail if l.role == 'tool'])
        return 'full' if tool_count >= 3 else 'note'

    @api.model
    def _handover_suggestions(self, user=None, limit=20):
        """Implicita förslag: sessioner som slutade mitt i en tur (D4).

        Ingen state — en FRÅGA vid läsning. "Avbruten" är negationen av
        "avslutad", och har TRE former (mätt 2026-10-14: den gamla
        definitionen fångade 2 av 10 avbrott):

          - sista raden är en tool-rad utan efterföljande assistant-rad
          - sista raden är en bash-körning (bashExecution)
          - sista raden är en assistant-rad med stopReason = toolUse

        Klassificeringen är STRUKTURELL, inte tidsberoende: det finns
        ingen tröskel som avgör när en session "räknas" som avbruten.
        Precis som Pi:s /resume listar sessioner utan tidsgräns.

        Sorteras nyast → äldst. Ett förslag åldras ALDRIG bort — det
        sorteras sist, men raderas inte.
        """
        user = user or self.env.user
        domain = self._handover_owned_domain(user) + [
            ('handover_state', '=', 'none'),
            ('init_type', 'not in', list(self.HANDOVER_AUTO_INIT_TYPES)),
        ]
        candidates = self.search(domain, limit=limit * 3,
                                 order='write_date desc')
        # OBS: bygg listan som en LISTA av id:n, inte som en recordset.
        # `out |= session` sorterar om efter modellens _order
        # (create_date desc) — den ordning vi byggde försvinner tyst.
        ids = []
        for session in candidates:
            if session._handover_is_aborted():
                ids.append(session.id)
            if len(ids) >= limit:
                break
        # browse() bevarar ordningen i id-listan (Odoo 18).
        return self.browse(ids)

    def _handover_is_aborted(self):
        """Är sessionens sista tur avbruten (någon av de tre formerna)?

        Läser sessionens sista rad. `role` är user/assistant/tool/system
        i session.line; Pi:s `bashExecution` och `toolResult` persisteras
        som tool-rader, och `stopReason=toolUse` bärs av assistant-raden.
        """
        self.ensure_one()
        lines = self.session_line_ids.sorted('sequence')
        if not lines:
            return False
        last = lines[-1]
        if last.role == 'tool':
            # Form 1 och 2: verktygssvar (eller bash-körning) utan
            # efterföljande assistantsvar.
            return True
        if last.role == 'assistant':
            # Form 3: modellen bad om ett verktyg — och dog.
            # Odoo:s vokabulär är `tool_calls` (Pi:s `stopReason` heter
            # `toolUse`; samma tillstånd, olika namn).
            return (last.finish_reason or '') in ('tool_calls', 'toolUse')
        return False

    def mark_done(self, reason='stop'):
        for session in self:
            try:
                session._write_final_summary()
            except Exception:
                # Eftermälet får aldrig hindra stängningen.
                _logger.exception(
                    'Aftermath failed at mark_done for session %s',
                    session.id)
        self.status = 'done'
        self.finish_reason = reason
        self.end_date = fields.Datetime.now()
        self._push_done_notification()

    # ── Klientdriven stängning (session-observability) ────────────────────
    #
    # `mark_done` anropas bara från webhook-vägen, och `mark_interrupted`
    # aldrig i drift. Pi:s `session_shutdown` är därför den ENDA signalen om
    # varför en session tar slut — och den nådde aldrig hit. Här tas den
    # emot: stängningen blir en `close_event`-rad (historik, append-only),
    # inte bara ett tillstånd som skrivs över.

    #: Vilken `finish_reason` en stängningsorsak motsvarar. `quit`/`reload`
    #: är normala avslut; `new`/`fork`/`resume` betyder att Pi bytte session.
    CLOSE_TO_FINISH = {
        'quit': 'closed',
        'reload': 'closed',
        'new': 'new_session',
        'resume': 'new_session',
        'fork': 'new_session',
        'timeout': 'timeout',
        'idle': 'idle',
        'error': 'error',
        'unknown': 'closed',
    }

    def _close_from_client(self, reason='unknown', at=None):
        """Stäng sessionen på klientens begäran. Returnerar skapade rader.

        Idempotent: samma `(session, reason, at)` ger ingen ny rad. En
        stängning som redan finns rör sessionen inte heller — annars skulle
        en upprepad signal skriva om `end_date` och se ut som en ny
        stängning i statistiken.

        `at` är klientens tidsstämpel (när Pi faktiskt stängdes), inte när
        Odoo tog emot anropet — nätverksfördröjning ska inte flytta
        händelsen.
        """
        self.ensure_one()
        at = at or fields.Datetime.now()
        CloseEvent = self.env['ai.coworker.session.close_event'].sudo()
        existing = CloseEvent.search([
            ('session_id', '=', self.id),
            ('reason', '=', reason),
            ('at', '=', at),
        ], limit=1)
        if existing:
            _logger.debug(
                'session %s: close event %s at %s already recorded',
                self.id, reason, at)
            return 0
        CloseEvent.create({
            'session_id': self.id,
            'reason': reason,
            'at': at,
            'pi_session_id': self.pi_session_id or False,
        })
        finish = self.CLOSE_TO_FINISH.get(reason, 'closed')
        # `end_date` är sessionens slut-tidsstämpel (det finns inget
        # `end_at`-fält; tidsstämpeln bor i `close_event.at`).
        self.sudo().write({
            'status': 'done',
            'finish_reason': finish,
            'end_date': at,
        })
        _logger.info(
            'session %s closed by client: reason=%s at=%s',
            self.id, reason, at)
        return 1

    def _reopen_if_closed(self, reason='resume'):
        """Återöppna en `done`-session när en ny tur kommer.

        VARFÖR (session-observability): `_find_or_create_coworker_session`
        hittar sessionen på `pi_session_id` och skriver nya turer UTAN att
        röra status. En session som en gång stängts (av cronen eller
        klienten) låg därför kvar som `done` med en `end_date` som inte
        längre stämde — och idle-cronen, som letar `status='active'`, hittade
        den ALDRIG igen. Det var mekanismen bakom "vi stänger nästan aldrig".

        `error` lämnas orörd: ett fel ska inte maskeras av att användaren
        frågar igen. En ny session skapas då hellre än att felraden skrivs
        över.
        """
        self.ensure_one()
        if not self.exists() or self.status != 'done':
            return False
        self.sudo().write({'status': 'active', 'end_date': False})
        _logger.info(
            'session %s reopened by a new turn (reason=%s)',
            self.id, reason)
        return True

    #: Rangordning av finish_reason — hÖgre vinner när två källor säger olika.
    #: Gatewayens `stop_reason` kan vara starkare än sessionens egen (t.ex. en
    #: trunkering `length` som Odoo missade medan den stängde på `max_rounds`).
    FINISH_REASON_RANK = {
        'stop': 0,
        'idle': 0,
        'closed': 0,
        'new_session': 0,
        'cancelled': 1,
        'interrupted': 1,
        'max_rounds': 2,
        'content_filter': 3,
        'length': 4,
        'error': 5,
    }

    def _raise_finish_reason(self, candidate):
        """Höj sessionens `finish_reason` om kandidaten är starkare.

        Gatewayens `stop_reason` (bifrost-session-lankning 6.1) kan veta något
        Odoo inte visste — t.ex. att modellen trunkerades (`length`) medan
        sessionen stängdes som `max_rounds`. Svagare värden skriver aldrig
        över ett starkare. Tom/okänd kandidat ignoreras.
        """
        if not candidate:
            return
        current = self.finish_reason or ''
        cur_rank = self.FINISH_REASON_RANK.get(current, -1)
        new_rank = self.FINISH_REASON_RANK.get(candidate, -1)
        if new_rank > cur_rank:
            self.finish_reason = candidate

    def _push_done_notification(self):
        """Web push "svar klart" till sessionens användare (via web_pwa_push).

        Tyst om användaren saknar enheter eller VAPID-nycklar saknas.
        """
        try:
            for session in self:
                if not session.user_id:
                    continue
                coworker_name = session.coworker_id.name or 'AI-medarbetare'
                self.env['web.pwa.push']._push_user_notification(
                    session.user_id,
                    title='AI Chat',
                    body='%s har svarat.' % coworker_name,
                    url='/ai/chat')
        except Exception:
            _logger.exception('web_pwa_push: session done push failed')

    # ── Durable Resume (OpenWorker-inspired) ──
    resumable = fields.Boolean('Resumable', default=True,
                                help='Can this session be resumed after interruption?')
    resumed_from_id = fields.Many2one('ai.coworker.session', string='Resumed From',
                                       help='Parent session this was resumed from')

    def mark_interrupted(self):
        """Mark session as interrupted (crash/stop) but resumable.

        Eftermälet skrivs även här (session-close krav 3): en avbruten
        session är den VANLIGASTE idag — `mark_done()` anropas bara från
        webhook-vägen — så utan detta får majoriteten aldrig ett eftermäle.
        """
        for session in self:
            try:
                session._write_final_summary()
            except Exception:
                _logger.exception(
                    'Aftermath failed at mark_interrupted for '
                    'session %s', session.id)
        self.status = 'active'  # Keep active so it can be resumed
        self.finish_reason = 'interrupted'
        self.end_date = fields.Datetime.now()
        _logger.info('Session %s marked interrupted (resumable)', self.name)

    @api.model
    def message_new(self, msg_dict, custom_values=None):
        """Inkommande mail → skapa session + kör medarbetaren på mailinnehållet.

        Mailgateway anropar detta när mail anländer till aliaset
        (alias@företagets-domän). alias_defaults sätter coworker_id
        (= ai_agent). Skapar sessionen och delegerar till _dispatch_mail.
        """
        defaults = dict(custom_values or {})
        # Stödjer både nytt (coworker_id) och gammalt (ai_coworker_id) alias-default
        coworker_id = (defaults.get('coworker_id')
                       or defaults.get('ai_coworker_id'))
        coworker = self.env['ai.coworker'].browse(coworker_id)
        subject = msg_dict.get('subject') or 'Inkommande mail'
        # Mailgateway förväntar sig att message_new skapar recordet
        session = self.with_context(mail_create_nosubscribe=True).create(defaults)
        if not coworker:
            session.name = subject
            return session
        if subject:
            session.name = subject[:80]
        return session._dispatch_mail(coworker, msg_dict)

    def _dispatch_mail(self, coworker, msg_dict):
        """Dispatch ett mail till rätt mail_action och posta svaret.

        Anropas av message_new (mailgateway) och mail-test-wizard:en (efter
        att bilagor lagts som minnen på sessionen).
        """
        subject = msg_dict.get('subject') or ''
        body = msg_dict.get('body') or ''
        mail_its = coworker.init_type_ids.filtered(
            lambda it: it.init_type == 'mail' and it.enabled)
        mail_it = (mail_its.filtered(lambda it: it.mail_action != 'reply')[:1]
                   or mail_its[:1])
        action = mail_it.mail_action if mail_it else 'reply'
        delay = mail_it.mail_reply_delay if mail_it else 0

        try:
            if action in ('invoice_ai', 'process'):
                reply_text = self._process_mail_generic(
                    coworker, msg_dict)
            elif action == 'create_record':
                reply_text = self._process_create_record(
                    coworker, msg_dict, mail_it)
            else:
                prompt = f"{subject}\n\n{body}".strip()
                # Extern dispatch frigör mail-tråden (cron-arbetartrad 4.3):
                # en extern agent svarar asynkront; svaret postas av
                # `_post_pending_reply` när processen är klar.
                if coworker._dispatch_or_loop(self, prompt, 'mail'):
                    _logger.info(
                        'Mail for session %s dispatched externally', self.id)
                    return self
                # B8: AUTO-mode kräver en HMAC-signatur bunden till
                # sessionen — en naken flagga ignoreras (och loggas).
                reply = coworker.with_context(
                    _ai_context_model='ai.coworker.session',
                    _ai_context_id=self.id,
                    **coworker._sign_auto_approve(self)).run(
                        prompt, session=self)
                reply_text = reply or 'Klart — inget svar genererades.'

            if delay and delay > 0:
                self.pending_reply = reply_text
                self.reply_at = fields.Datetime.now() + timedelta(
                    minutes=delay)
                _logger.info('Mail reply delayed %s min for session %s',
                             delay, self.id)
            else:
                self.message_post(
                    body=reply_text,
                    subtype_xmlid='mail.mt_comment',
                    message_type='comment',
                )
        except Exception as e:
            _logger.warning('mail processing failed for session %s: %s',
                            self.id, e)
            self.message_post(
                body=f'Fel vid bearbetning av mailet: {e}',
                subtype_xmlid='mail.mt_comment',
                message_type='comment',
            )
        return self

    def _post_pending_reply(self):
        """Posta fördröjda mail-svar (anropas av cron). Idempotent."""
        now = fields.Datetime.now()
        due = self.search([
            ('pending_reply', '!=', False),
            ('reply_at', '!=', False),
            ('reply_at', '<=', now),
        ])
        for session in due:
            try:
                session.message_post(
                    body=session.pending_reply,
                    subtype_xmlid='mail.mt_comment',
                    message_type='comment',
                )
                session.write({'pending_reply': False, 'reply_at': False})
                _logger.info('Delayed mail reply posted for session %s',
                             session.id)
            except Exception as e:
                _logger.warning('Could not post delayed reply %s: %s',
                                session.id, e)
        return len(due)

    @api.model
    def _process_watch_sessions(self):
        """Cron: processa watch-kön i egna transaktioner per session.

        Körs av ir.cron (cron_watch_process, 1 minut). Hämtar sessioner
        med watch_pending=True och kör coworkern med den sparade prompten
        via run(prompt, session=session) — sessionen återanvänds (ingen
        dubbel-session) och run() sätter status='done'/'error' själv.
        Varje session körs i en savepoint så en misslyckad session inte
        påverkar de andra. AI-körningen sker ALDRIG i base_automation-
        transaktionen (fix-watch-async).

        Returns:
            int: antal processade sessioner.
        """
        sessions = self.search([
            ('watch_pending', '=', True),
            ('status', '=', 'active'),
        ], limit=10)
        processed = 0
        for session in sessions:
            coworker = session.coworker_id
            if not coworker:
                try:
                    session.write(
                        {'watch_pending': False, 'status': 'error'})
                except Exception:
                    pass  # best-effort: tyst fallback — se etiketteringsregeln
                continue
            try:
                with self.env.cr.savepoint():
                    # Extern dispatch frigör cron-tråden (cron-arbetartrad
                    # 4.1/4.2): watch-transaktionen får inte blockeras av en
                    # 240 s LLM-körning när agenten är extern.
                    if coworker._dispatch_or_loop(
                            session, session.watch_prompt or '', 'watch'):
                        session.write({'watch_pending': False})
                        processed += 1
                        continue
                    coworker.with_context(
                        _ai_context_model=session.watch_model,
                        _ai_context_id=session.watch_res_id,
                        # B8 + F4 (init-type-execution-parity 2.5): watch är
                        # en obevakad väg precis som mail, men saknade
                        # signaturen. Utan den blev läget INTERACTIVE med
                        # handler=None — dvs. fail-open (verktyget kördes
                        # utan godkännande). Samma mekanism som mail-vägen.
                        **coworker._sign_auto_approve(session)
                    ).run(session.watch_prompt or '', session=session)
                session.write({'watch_pending': False})
                processed += 1
            except Exception as e:
                _logger.warning('Watch-session %s misslyckades: %s',
                                session.id, e)
                try:
                    session.write(
                        {'watch_pending': False, 'status': 'error'})
                except Exception:
                    pass  # best-effort: tyst fallback — se etiketteringsregeln
        return processed

    # ── Mail-trigger-flöden ──────────────────────────────────────────────

    def _process_create_record(self, coworker, msg_dict, mail_it):
        """Skapa/uppdatera ett record i målmodellen från mailinnehållet."""
        subject = msg_dict.get('subject') or 'Inkommande mail'
        body = msg_dict.get('body') or ''
        target = mail_it.mail_target_model_id.model if mail_it and \
            mail_it.mail_target_model_id else False
        prompt = (
            f'{subject}\n\n{body}'.strip()
            + (f'\n\nSkapa/uppdatera ett record i modellen {target} '
               'med odoo_search/odoo_create/odoo_write. Svara kort med vad '
               'du gjorde och recordets id.' if target else '')
        )
        # B8: signerad AUTO-mode (se _sign_auto_approve).
        return coworker.with_context(
            _ai_context_model='ai.coworker.session',
            _ai_context_id=self.id,
            **coworker._sign_auto_approve(self)).run(prompt, session=self)

    def _process_mail_generic(self, coworker, msg_dict):
        """Generiskt mail-flöde (process/invoice_ai) — körs av coworkern själv.

        Kapaciteterna lever som SKILLS på medarbetarens agenter (inte som
        force-körda sub-agenter) — t.ex.:
        1. Skill 'Mail: Hitta/skapa res.partner' — avsändaren → partner
        2. Skill 'Mail: Leverantörsfaktura → account.move' — OCR + skapa move
        Medarbetaren körs EN gång med mail + bilagetext som kontext.
        """
        # Deterministisk snabb-sök av partner (sparar ett verktygsanrop);
        # hittas ingen skapar LLM:en via skill:en.
        partner = self._resolve_mail_partner(msg_dict)

        attach_text = self._attachment_text(msg_dict)
        email, sender_name = self._sender_from_msg(msg_dict)
        subject = msg_dict.get('subject') or ''
        body = msg_dict.get('body') or ''
        prompt = (
            'A vendor bill has arrived by email. Process it according to your '
            'skills.\n\n'
            f'Sender: {email or "unknown"} '
            f'({sender_name or "unknown name"})\n'
            f'Matching res.partner: {partner.name if partner else "none — "}'
            f'{"find/create via your partner skill" if not partner else ""} '
            f'(id={partner.id if partner else "?"})\n'
            f'Email subject: {subject}\n'
            f'Email body:\n{body}\n\n'
            f'Bill text (OCR/extracted attachment):\n'
            f'{attach_text or "(no attachment — analyse the email body)"}\n\n'
            'STEPS (follow your skills):\n'
            '1. Find/create the res.partner for the sender (odoo_search/odoo_create).\n'
            '2. Create an account.move (move_type=in_invoice) with the right '
            'partner, journal (type=purchase), ref (bill number), date, taxes '
            'and lines from the bill text.\n'
            '3. Use odoo_call_method (action_post) IF everything looks '
            'correct — otherwise leave it in draft.\n'
            'Answer briefly: what you created, the account.move id and the '
            'amount.'
        )
        # B8: signerad AUTO-mode (se _sign_auto_approve).
        reply = coworker.with_context(
            _ai_context_model='ai.coworker.session',
            _ai_context_id=self.id,
            **coworker._sign_auto_approve(self)).run(prompt, session=self)
        return (
            f'Vendor bill processed. Sender: '
            f'{partner.name if partner else sender_name or email or "unknown"}.\n'
            f'{reply}'
        )

    def _sender_from_msg(self, msg_dict):
        """Extrahera (email, namn) ur From-header: 'Namn <a@b.se>'."""
        import re
        raw = msg_dict.get('email_from') or msg_dict.get('from') or ''
        m = re.search(r'[\w.+-]+@[\w.-]+', raw)
        email = m.group(0) if m else ''
        nm = re.match(r'^([^<]+)<', raw or '')
        name = nm.group(1).strip().strip('"\'') if nm else ''
        return email, name

    def _resolve_mail_partner(self, msg_dict):
        """Hitta/skapa res.partner från avsändaren (inbyggd, robust).

        1. Email-sökning (email / email_normalized)
        2. Namn-sökning (exakt, sedan ilike — företagsnamn)
        3. Deterministisk skapelse (is_company=True, leverantörs-mail)

        Används av mail-flödet; LLM:en behöver inte skapa partnern.
        """
        email, name = self._sender_from_msg(msg_dict)
        partner = self.env['res.partner']
        if email:
            partner = self.env['res.partner'].search(
                ['|', ('email', '=', email),
                 ('email_normalized', '=', email.lower())],
                limit=1)
        if not partner and name:
            partner = self.env['res.partner'].search(
                [('name', '=', name)], limit=1)
            if not partner:
                partner = self.env['res.partner'].search(
                    [('name', 'ilike', name)], limit=1)
        if not partner:
            partner = self.env['res.partner'].create({
                'name': name or (email.split('@')[0]
                                 if email else 'Unknown sender'),
                'email': email or False,
                'is_company': True,
            })
            _logger.info('Created res.partner %s (%s) from the mail sender',
                         partner.id, email or name)
        elif partner.email != email and email:
            # Uppdatera email om den saknades/förändrats
            try:
                partner.write({'email': email})
            except Exception:
                pass  # best-effort: tyst fallback — se etiketteringsregeln
        return partner

    def _attachment_text(self, msg_dict, max_chars=12000):
        """Extrahera text ur mail-bilagor (PDF → text, eller råtext)."""
        text_parts = []
        att_ids = msg_dict.get('attachment_ids') or []
        for att_id in att_ids:
            try:
                att = self.env['ir.attachment'].browse(att_id)
                if att.mimetype == 'application/pdf':
                    import io
                    from pypdf import PdfReader
                    reader = PdfReader(io.BytesIO(att.raw))
                    page_text = '\n'.join(
                        (p.extract_text() or '') for p in reader.pages)
                    if page_text.strip():
                        text_parts.append(page_text.strip())
                elif att.mimetype and att.mimetype.startswith('text/'):
                    text_parts.append(
                        att.raw.decode('utf-8', errors='replace'))
            except Exception as e:
                _logger.warning('Could not read attachment %s: %s', att_id, e)
        return '\n\n---\n\n'.join(text_parts)[:max_chars]

    def resume_session(self):
        """Create a new session that continues from this interrupted one.

        Returns a new session with the same coworker/agent/identity config,
        linked via resumed_from_id. The calling code should re-run the
        AgentLoop with the history from this session.
        """
        self.ensure_one()
        if not self.resumable:
            return None

        new_session = self.create({
            'coworker_id': self.coworker_id.id,
            'agent_id': self.agent_id.id,
            'identity_id': self.identity_id.id,
            'status': 'active',
            'init_type': self.init_type or 'manual',
            'config_json': self.config_json,
            'user_id': self.user_id.id,
            'resumed_from_id': self.id,
        })
        _logger.info('Resumed session %s from %s', new_session.name, self.name)
        return new_session
