# -*- coding: utf-8 -*-
"""ai.coworker.session — standalone session model for agent runs."""

import json, logging, re, uuid
from datetime import timedelta
from odoo import models, fields, api

_logger = logging.getLogger(__name__)

# Resolver-registry för kostnadskontext (D9): {nyckel → callable(session)}.
# Callable tar en ai.coworker.session och härleder partner_id (och ev.
# domänfält). Bryggor registrerar strategier vid modulimport — core
# förblir domän-rent (inga modellreferenser här).
_COST_CONTEXT_STRATEGIES = {}


def register_cost_context_strategy(key, fn):
    """Registrera en resolver-strategi (nyckel → callable(session))."""
    _COST_CONTEXT_STRATEGIES[key] = fn


def get_cost_context_strategy(key):
    """Hämta en registrerad resolver-strategi (eller None)."""
    return _COST_CONTEXT_STRATEGIES.get(key)


class AICoworkerSession(models.Model):
    _name = 'ai.coworker.session'
    _inherit = ['mail.thread']
    _description = 'AI Session'
    _order = 'create_date desc'

    ai_task_id = fields.Many2one('ai.org.task', string='AI Task',
        help='AI-org-uppgiften (ai.org.task) som denna session arbetar på. '
             'Skapas automatiskt vid checkout. OBS: skilj från task_id '
             '(project.task) som läggs av project_ai-bryggan.')

    name = fields.Char(default=lambda self: str(uuid.uuid4())[:8])
    coworker_id = fields.Many2one('ai.coworker', string='Coworker', ondelete='cascade')
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
        ('openai_api', 'OpenAI API'),
        ('watch', 'Watch — Dataändring'),
    ], string='Start via', index=True, readonly=True, tracking=True,
        help='Hur sessionen startades (kanal/källa). Fylls vid sessionens '
             'skapande där källan är känd.')
    user_rating = fields.Selection([
        ('down', 'Missnöjd'),
        ('neutral', 'Neutral'),
        ('up', 'Nöjd'),
        ('unassessed', 'Ej bedömd'),
    ], string='Användarbedömning', default='unassessed',
        help='Användarens subjektiva bedömning av svaret (feedback för att '
             'förbättra coworkern över tid).')
    error_detail = fields.Text('Felbeskrivning', readonly=True,
        help='Fylls när sessionen avslutas med status error-ish (undantagsmeddelande '
             'för felsökning och analys).')

    # ── Extern körning (external-agent-runtime D6/D10) ───────────────
    # Mätpunkten: varje extern körning loggar PID, minne, starttid och
    # varaktighet. Tröskeln för samtidighet är EMPIRISK och sätts inte nu —
    # men ska kunna avläsas ur dessa fält när frågan uppstår.
    external_pid = fields.Integer('Extern PID', readonly=True,
        help='PID för den externa agent-processen (om runtime=external).')
    external_port = fields.Integer('Extern port', readonly=True)
    external_started_at = fields.Datetime('Extern starttid', readonly=True)
    external_rss_kb = fields.Integer('Extern RSS (kB)', readonly=True,
        help='Processens RSS vid dispatch — mätpunkten för minnesprofilen.')
    external_spawn_time = fields.Float('Spawn-tid (s)', readonly=True,
        help='Kall start-tid i sekunder (D9: ska vara under ~1 s).')
    external_duration = fields.Float('Varaktighet (s)', readonly=True,
        help='Körningens varaktighet i sekunder.')

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
                'Extern process %s kunde inte dödas.' % self.external_pid,
        })
        _logger.info('session %s: avbröt extern pid %s (borta=%s)',
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
                    'extern städning: dödade föräldralös pid %s '
                    '(session %s, status %s)',
                    sess.external_pid, sess.name, sess.status)
            elif alive and sess.status in ('active', 'draft') \
                    and sess.external_started_at \
                    and sess._external_elapsed() > limit:
                rt.kill_pid(sess.external_pid)
                sess._finalize_external_run()
                sess.sudo().write({
                    'status': 'error',
                    'error_detail': 'Extern körning överskred tidsgränsen '
                                    '(%.0f s > %.0f s).' %
                                    (sess._external_elapsed(), limit),
                })
                timed_out += 1
                _logger.warning(
                    'extern timeout: pid %s (session %s) körde %.0f s > '
                    '%.0f s — avbruten',
                    sess.external_pid, sess.name,
                    sess._external_elapsed(), limit)
            elif not alive and sess.status in ('active', 'draft'):
                sess._finalize_external_run()
                sess.sudo().write({
                    'status': 'error',
                    'error_detail': 'Extern process %s avslutades oväntat.'
                                    % sess.external_pid,
                })
                detected += 1
                _logger.warning(
                    'extern livs-heartbeat: pid %s är död men session %s '
                    'är %s — markerad error',
                    sess.external_pid, sess.name, sess.status)
        if reaped or detected or timed_out:
            _logger.info('extern städning: %d föräldralösa dödade, %d '
                         'döda upptäckta, %d timeouts',
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
        help='Satt av _trigger_watch — sessionen väntar på asynkron '
             'bearbetning av cron (_process_watch_sessions).')
    watch_prompt = fields.Text(
        'Watch Prompt',
        help='Prompt som genererades vid watch-trigger — körs av cron.')
    watch_model = fields.Char(
        'Watch Model',
        help='Modellnamn (record._name) som triggade watchen.')
    watch_res_id = fields.Integer(
        'Watch Record ID',
        help='Record-id som triggade watchen.')

    config_json = fields.Text('Configuration')
    history_json = fields.Text('Message History')

    # Buzz session summary (change ai-orchestration-tidy-up 7.4)
    summary = fields.Text(
        'Session Summary',
        help='LLM-genererad sammanfattning av konversationen — injiceras som '
             'kontext till nya agenter när tröskeln passeras.')
    summary_message_count = fields.Integer(
        'Summary Message Count', default=0,
        help='Antal meddelanden vid senaste sammanfattningen.')

    memory_extracted = fields.Boolean(
        'Memory Extracted', default=False,
        help='Har sessionens erfarenhet extraherats till personligt minne? '
             'Gör bron idempotent — eftermälet kan anropas från flera håll '
             '(mark_done, idle-cron, buzz) utan dubbla LLM-anrop.')

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
        help='Pi-sessionens UUID (1:1 mot Pi-sessionen). Resumé av samma '
             'Pi-session återfinner samma Odoo-session.')
    pi_message_count = fields.Integer(
        'Pi Messages Persisted', default=0, copy=False,
        help='Antal poster i Pi-klientens messages[] som redan sparats som '
             'session lines. Pi skickar HELA historiken varje anrop; denna '
             'räknare gör att endast NYA meddelanden persisteras (en line '
             'per messages[]-post, ingen duplicering).')
    partner_id = fields.Many2one(
        'res.partner', string='Kund', index=True,
        help='Kund (res.partner) som sessionens kostnad belastar. Härleds '
             'normalt från projektets partner via coworkerns '
             'kostnadskontext-strategi.')
    cost_context_confirmed = fields.Boolean(
        'Kostnadskontext bekräftad', default=False,
        help='Sätts när kostnadsbelastningen bekräftats (en gång per '
             'session). Redan bekräftad session frågar inte om igen '
             '(inte heller efter resume/fork-kopiering).')

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
                                     copy_from_pi_session_id=''):
        """Find-or-create session via pi_session_id (session-cost-context).

        Används av POST /ai/v1/sessions/lookup och openai_api-vägen.
        Idempotent: samma pi_session_id → samma session. Vid
        copy_from_pi_session_id (fork) skapas en NY session med kontexten
        (project/task/partner + cost_context_confirmed) kopierad från
        källsessionen.

        Returnerar (session, created: bool).
        """
        pi_session_id = (pi_session_id or '').strip().lower()
        session = self.search(
            [('pi_session_id', '=', pi_session_id)], limit=1)
        if session:
            return session, False
        vals = {
            'pi_session_id': pi_session_id,
            'status': 'active',
            'init_type': 'openai_api',
            'name': pi_session_id[:8],
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
                return session, False
            # pi_session_id skickat men okänt → NY session (ingen fallback).
            return self.create({
                'coworker_id': coworker_id,
                'status': 'active',
                'init_type': 'openai_api',
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
            return session, False
        return self.create({
            'coworker_id': coworker_id,
            'status': 'active',
            'init_type': 'openai_api',
            'name': (prompt or 'API')[:80],
            'user_id': int(user_id or 0),
        }), True

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
    def _persist_pi_messages(self, env, session, messages,
                             input_t=0, output_t=0, model_real=''):
        """Skriv NYA messages[]-poster som session lines (en per post).

        Returnerar antalet skapade rader. Tyst no-op vid fel (persist får
        aldrig krascha en körning).
        """
        try:
            messages = messages or []
            total = len(messages)
            already = int(session.pi_message_count or 0)
            # Robusthet: om klienten skickar färre meddelanden än vi sparat
            # (t.ex. trunkerad historik) börjar vi om från 0 så inget tappas.
            if already > total:
                already = 0
            new_messages = messages[already:]
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
                    pass

            # Index för sista nya user-/assistant-raden (token-bokföring).
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

            last_user_idx = -1
            last_asst_idx = -1
            for i, m in enumerate(new_messages):
                role = _norm_role(m)
                if role == 'user':
                    last_user_idx = i
                elif role == 'assistant':
                    last_asst_idx = i

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
                if (last_line and i == 0 and last_line.role == role
                        and (last_line.content or '') == content):
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
                # Tool-meddelanden: OpenAI:s tool_call_id → spara som namn.
                if role == 'tool':
                    vals['tool_name'] = m.get('name') or m.get('tool_call_id') or ''
                if m.get('tool_calls'):
                    vals['tool_calls'] = json.dumps(
                        m.get('tool_calls'), ensure_ascii=False)
                # Token-bokföring (endast sista nya user/assistant).
                if i == last_user_idx and input_t:
                    vals['token_input'] = input_t
                if i == last_asst_idx and output_t:
                    vals['token_output'] = output_t
                Line.create(vals)
                created_count += 1

            session.write({
                'pi_message_count': total,
                'token_input': (session.token_input or 0) + input_t,
                'token_output': (session.token_output or 0) + output_t,
            })
            return created_count
        except Exception as e:
            _logger.warning('pi message persist failed: %s', e)
            return 0

    create_date = fields.Datetime('Started', default=lambda self: fields.Datetime.now())
    end_date = fields.Datetime('Ended')
    round_count = fields.Integer('Rounds', default=0)
    finish_reason = fields.Char('Finish Reason')

    user_id = fields.Many2one('res.users', default=lambda self: self.env.user)
    company_id = fields.Many2one('res.company', default=lambda self: self.env.company)

    # Mail-svar med dröjsmål (mail-trigger): svarstexten postas av cron
    # när reply_at passerats.
    pending_reply = fields.Text('Pendande mail-svar',
        help='Svarstext som postas av cron efter svarsdröjsmålet.')
    reply_at = fields.Datetime('Svara efter',
        help='Tidpunkt då det fördröjda svaret ska postas.')

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
        'Öppna HITL', compute='_compute_hitl_open_count')
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
        'Öppna frågor',
    )

    def _final_summary_prompt(self, transcript):
        """Strukturerad, svensk prompt (D4 / session-close krav 5).

        Rubrikerna är fasta eftersom eftermälet är konsolideringens
        råvara: samma form varje gång gör den jämförbar och sökbar med
        svensk fulltextsökning.
        """
        headings = '\n'.join('### %s' % h for h in self.SUMMARY_HEADINGS)
        return (
            'Sammanfatta sessionen nedan på svenska. Använd exakt dessa '
            'rubriker och inga andra:\n\n%s\n\n'
            'Utelämna en rubrik helt om avsnittet inte har något innehåll — '
            'skriv aldrig påhittat innehåll för att fylla ut. Var koncis men '
            'komplett: fakta, beslut och öppna frågor är det som spelar roll.\n\n'
            '--- KONVERSATION ---\n%s' % (headings, transcript))

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
                'Eftermäle: session %s oförändrad (%d rader) — hoppar över',
                self.id, total)
            return self.summary

        # 2. För kort session → ingen tom sammanfattning.
        if total < self.MIN_SUMMARY_LINES:
            _logger.debug(
                'Eftermäle: session %s för kort (%d < %d rader) — '
                'ingen sammanfattning',
                self.id, total, self.MIN_SUMMARY_LINES)
            return None

        transcript = self._final_summary_transcript(lines)
        if not transcript.strip():
            _logger.debug(
                'Eftermäle: session %s har inget innehåll — hoppar över',
                self.id)
            return None

        summary = self._run_final_summary_llm(transcript)
        if not summary:
            _logger.warning(
                'Eftermäle: LLM-sammanfattning misslyckades för session %s '
                '(%d rader) — summary_message_count lämnas orörd så att '
                'nästa försök tar om', self.id, total)
            return None

        self.sudo().write({
            'summary': summary,
            'summary_message_count': total,
        })
        _logger.info(
            'Eftermäle skrivet för session %s (%d rader, %d tecken)',
            self.id, total, len(summary))

        # Bron till det personliga minnet (session-memory-bridge D2).
        # Eftermälet är den naturliga platsen: här finns både transcriptet
        # och vetskapen att sessionen är slut. Anropet är idempotent.
        self._bridge_to_personal_memory()
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
                'Minne: session %s redan extraherad — hoppar över', self.id)
            return 0

        # 2. Tröskel — samma som eftermälet
        total = len(self.session_line_ids)
        if total < self.MIN_SUMMARY_LINES:
            _logger.debug(
                'Minne: session %s för kort (%d < %d rader) — ingen '
                'extraktion', self.id, total, self.MIN_SUMMARY_LINES)
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
                'Minne: session %s — dispatch-upplösning föll (%s), '
                'faller tillbaka på sessionens user_id', self.id, e)

        if not user and self.user_id:
            user = self.user_id

        if not user:
            _logger.warning(
                'Minne: session %s saknar användare — ingen extraktion',
                self.id)
            return 0

        if user == self.env.ref('base.user_root'):
            _logger.warning(
                'Minne: session %s upplöstes till systemuser — ingen '
                'extraktion (personligt minne kräver en riktig användare)',
                self.id)
            return 0

        try:
            count = self.env['ai.personal.memory'].sudo().extract_from_session(
                self.id)
        except Exception as e:
            _logger.error(
                'Minne: extraktion från session %s misslyckades: %s',
                self.id, e, exc_info=True)
            return 0

        # Markera ÄVEN vid 0 extraherade — annars kör varje eftermäle om
        # samma LLM-anrop för en session som inte gav något.
        self.sudo().write({'memory_extracted': True})
        _logger.info(
            'Minne: session %s extraherade %d minnen till användare %s',
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
                ProviderFactory, get_default_provider, get_default_model_name)
            from odoo.addons.ai_agent_core.core.loop import (
                AgentLoop, AgentConfig)
            quest = self.coworker_id
            provider, model_rec = (
                ProviderFactory.from_coworker(quest) if quest else (None, None))
            if not provider:
                provider, model_rec = get_default_provider()
            if not provider:
                _logger.warning(
                    'Eftermäle: ingen provider tillgänglig för session %s',
                    self.id)
                return None
            model_name = (model_rec and model_rec._get_api_name()) \
                or get_default_model_name()
            loop = AgentLoop(provider=provider, tools=[], config=AgentConfig(
                model=model_name, max_rounds=1, max_tokens=2048))
            result = asyncio.run(
                loop.run(self._final_summary_prompt(transcript)))
            return (result.text or '').strip()[:4000] or None
        except Exception as e:
            _logger.warning(
                'Eftermäle: sammanfattning misslyckades för session %s: %s',
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
                        'Session %s stängs TOM (0 rader) — coworker=%s '
                        'init_type=%s skapad=%s. En session ska bära en '
                        'körning; den här gjorde det inte.',
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
                session.sudo().write({
                    'status': 'done',
                    'finish_reason': 'idle',
                    'end_date': fields.Datetime.now(),
                })
                closed += 1
            except Exception:
                _logger.exception(
                    'Idle-cron: kunde inte stänga session %s', session.id)
        if closed:
            _logger.info(
                'Idle-cron: stängde %d sessioner utan aktivitet i %d min '
                '(%d av dem var tomma)',
                closed, idle_minutes, empty)
        return closed

    def mark_done(self, reason='stop'):
        # Eftermälet skrivs INNAN statusen sätts: det som hann hända är
        # kunskap (session-close krav 3), och en 'done'-session ska redan
        # vara sammanfattad om någon läser den direkt efteråt.
        for session in self:
            try:
                session._write_final_summary()
            except Exception:
                # Eftermälet får aldrig hindra stängningen.
                _logger.exception(
                    'Eftermäle misslyckades vid mark_done för session %s',
                    session.id)
        self.status = 'done'
        self.finish_reason = reason
        self.end_date = fields.Datetime.now()
        self._push_done_notification()

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
                    'Eftermäle misslyckades vid mark_interrupted för '
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
                reply = coworker.with_context(
                    _ai_context_model='ai.coworker.session',
                    _ai_context_id=self.id,
                    _ai_auto_approve=True).run(prompt, session=session)
                reply_text = reply or 'Klart — inget svar genererades.'

            if delay and delay > 0:
                self.pending_reply = reply_text
                self.reply_at = fields.Datetime.now() + timedelta(
                    minutes=delay)
                _logger.info('Mail-svar fördröjt %s min för session %s',
                             delay, self.id)
            else:
                self.message_post(
                    body=reply_text,
                    subtype_xmlid='mail.mt_comment',
                    message_type='comment',
                )
        except Exception as e:
            _logger.warning('mail-bearbetning misslyckades för session %s: %s',
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
                _logger.info('Fördröjt mail-svar postat för session %s',
                             session.id)
            except Exception as e:
                _logger.warning('Kunde inte posta fördröjt svar %s: %s',
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
                    pass
                continue
            try:
                with self.env.cr.savepoint():
                    coworker.with_context(
                        _ai_context_model=session.watch_model,
                        _ai_context_id=session.watch_res_id,
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
                    pass
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
        return coworker.with_context(
            _ai_context_model='ai.coworker.session',
            _ai_context_id=self.id,
            _ai_auto_approve=True).run(prompt, session=self)

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
            'En leverantörsfaktura har kommit in via mail. Bearbeta den '
            'enligt dina skills.\n\n'
            f'Avsändare: {email or "okänd"} '
            f'({sender_name or "okänt namn"})\n'
            f'Hittad res.partner: {partner.name if partner else "ingen — "}'
            f'{"hitta/skapa via din partner-skill" if not partner else ""} '
            f'(id={partner.id if partner else "?"})\n'
            f'Mailämne: {subject}\n'
            f'Mailtext:\n{body}\n\n'
            f'Fakturatext (OCR/extraherad bilaga):\n'
            f'{attach_text or "(ingen bilaga — analysera mailtexten)"}\n\n'
            'STEG (följ dina skills):\n'
            '1. Hitta/skapa res.partner för avsändaren (odoo_search/odoo_create).\n'
            '2. Skapa account.move (move_type=in_invoice) med rätt partner, '
            'journal (type=purchase), ref (fakturanummer), datum, skatter och '
            'rader från fakturatexten.\n'
            '3. Använd odoo_call_method (action_post) OM allt ser korrekt ut '
            '— annars lämna i draft.\n'
            'Svara kort: vad du skapade, account.move-id:t och beloppet.'
        )
        reply = coworker.with_context(
            _ai_context_model='ai.coworker.session',
            _ai_context_id=self.id,
            _ai_auto_approve=True).run(prompt, session=self)
        return (
            f'Leverantörsfaktura bearbetad. Avsändare: '
            f'{partner.name if partner else sender_name or email or "okänd"}.\n'
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
                                 if email else 'Okänd avsändare'),
                'email': email or False,
                'is_company': True,
            })
            _logger.info('Skapade res.partner %s (%s) från mail-avsändare',
                         partner.id, email or name)
        elif partner.email != email and email:
            # Uppdatera email om den saknades/förändrats
            try:
                partner.write({'email': email})
            except Exception:
                pass
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
                _logger.warning('Kunde inte läsa bilaga %s: %s', att_id, e)
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
