# -*- coding: utf-8 -*-
"""Sessionens spår — utförandet, inte bara konversationen.

VARFÖR (session-observability): `ai.coworker.session.line` loggar VAD SOM
SADES. På Pi-vägen (`/ai/v1/chat/completions`) är Odoo en proxy för text:
Pi exekverar verktygen lokalt och skickar tillbaka sina egna meddelanden.
Följden var att Odoo inte kunde svara på vilka verktyg som kördes, av vilken
agent, med vilka skills, var de kördes eller hur länge — och därmed inte
heller optimera agenterna.

Tre sidotabeller kompletterar `line`:

| Tabell                              | Vad den bär                     |
|-------------------------------------|---------------------------------|
| `ai.coworker.session.agent_run`     | en rad per agentkörning         |
| `ai.coworker.session.tool_call`     | en rad per verktygsanrop        |
| `ai.coworker.session.close_event`   | en rad per stängning            |

⚠ DE ÄR SPÅR, INTE BUDGET. Ingen av dem får räknas in i
`session_line_count`, `burn_rate_mtokens_day` eller `monthly_cap_mtokens`.
Budgeten läser fortsatt `ai.coworker.session.line` (se
utfall-och-tokenmatning). Att summera `agent_run.tokens_in` OCH
`line.token_input` skulle dubbelräkna varje anrop.

⚠ KONVERSATIONEN BOR KVAR I `line`. De här tabellerna pekar på den
(`line_id`) i stället för att ersätta den, så web-UI:t, historiken till
LLM:en och tokenredovisningen är oförändrade.
"""

from odoo import api, fields, models


class AICoworkerSessionAgentRun(models.Model):
    """En agentkörning inom en session."""

    _name = 'ai.coworker.session.agent_run'
    _description = 'Session Agent Run'
    _order = 'session_id desc, turn_index desc, id desc'

    session_id = fields.Many2one(
        'ai.coworker.session', string='Session', required=True,
        ondelete='cascade', index=True,
        help='The session the run belongs to. The row follows the session '
             'lifecycle (cascade).')
    line_id = fields.Many2one(
        'ai.coworker.session.line', string='Message',
        ondelete='set null', index=True,
        help='The message line that started the run, so the trace can be read '
             'both per session and per message.')

    kind = fields.Selection([
        ('odoo_loop', 'Odoo Agent Loop'),
        ('pi_generation', 'Pi Generation (proxy)'),
        ('specialist', 'Specialist Delegation'),
        ('conference', 'Conference'),
    ], string='Kind', required=True, default='pi_generation', index=True,
        help='What kind of run the row describes. `pi_generation` is pure '
             'generation: no agent loop executes in Odoo; Pi owns the tool '
             'execution.')

    agent_id = fields.Many2one(
        'ai.agent', string='Agent', index=True,
        help='The agent the run is attributed to. On the Pi path this is the '
             "coworker's first agent — a dimension for aggregation, not a "
             'claim that an agent loop ran (see `kind`).')
    skill_ids = fields.Many2many(
        'ai.skill', 'ai_session_agent_run_skill_rel',
        'agent_run_id', 'skill_id', string='Skills',
        help='The skills actually activated for the run. The selection was made '
             'by `_select_relevant_skills` and previously existed only in '
             'the log.')

    model_requested = fields.Char(
        'Model Requested',
        help="The model that was requested (the coworker's agent chain).")
    model_served = fields.Char(
        'Model Served',
        help='The model that actually answered, when it differs — e.g. after a '
             'gateway fallback. Never overwritten by model_requested.')
    turn_index = fields.Integer(
        'Turn', default=0,
        help="The turn's order within the session (the client's `turn`, "
             '1-based). 0 = unknown.')

    started_at = fields.Datetime('Started')
    ended_at = fields.Datetime('Ended')

    tokens_in = fields.Integer('Input Tokens', default=0,
        help='TRACE, not budget: the budget reads session_line. ')
    tokens_out = fields.Integer('Output Tokens', default=0)
    cost_usd = fields.Float(
        'Cost (USD)', digits=(12, 8), default=0.0,
        help='The actual cost of the run. Never merged with `token_sys` (the '
             'billing basis).')
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
        help='The same vocabulary as the session and the line.')
    aborted = fields.Boolean('Aborted', default=False,
        help='True when the run was aborted (e.g. Escape in Pi), as opposed to '
             'having completed.')

    tool_call_ids = fields.One2many(
        'ai.coworker.session.tool_call', 'agent_run_id',
        string='Tool Calls')
    tool_call_count = fields.Integer(
        'Tool Calls', compute='_compute_tool_call_count')

    @api.depends('tool_call_ids')
    def _compute_tool_call_count(self):
        for rec in self:
            rec.tool_call_count = len(rec.tool_call_ids)


class AICoworkerSessionToolCall(models.Model):
    """Ett verktygsanrop inom en session."""

    _name = 'ai.coworker.session.tool_call'
    _description = 'Session Tool Call'
    _order = 'session_id desc, started_at desc, id desc'

    session_id = fields.Many2one(
        'ai.coworker.session', string='Session', required=True,
        ondelete='cascade', index=True)
    agent_run_id = fields.Many2one(
        'ai.coworker.session.agent_run', string='Agent Run',
        ondelete='set null', index=True,
        help='The run the call was made within.')
    line_id = fields.Many2one(
        'ai.coworker.session.line', string='Message',
        ondelete='set null', index=True,
        help='The message line the call belongs to.')

    tool_name = fields.Char('Tool', required=True, index=True)
    tool_call_id = fields.Char(
        'Tool Call ID', index=True,
        help="The client's/caller's id (OpenAI's tool_call_id). Dedup key: the "
             'same call twice yields one row.')
    parent_tool_call_id = fields.Char(
        'Parent Tool Call ID', index=True,
        help='Set when another tool made the call (e.g. a subagent or code '
             'call). Lets the subagent tree be reconstructed: which part of '
             'the work cost what.')
    executor = fields.Selection([
        ('pi_local', 'Pi (local)'),
        ('odoo', 'Odoo'),
        ('nats', 'NATS'),
    ], string='Executor', default='pi_local', required=True, index=True,
        help='Where the tool actually ran. Without this it is impossible to '
             'answer which tools are used and where — which is exactly the '
             'question that decides where the agents should improve.')

    args = fields.Json(
        'Arguments',
        help="The call's arguments (truncated). JSON, not a copy of the line's "
             'content.')
    result_preview = fields.Text(
        'Result (preview)',
        help="A preview of the result. Same cap as the line's content.")
    is_error = fields.Boolean('Error', default=False, index=True)
    duration_ms = fields.Integer('Duration (ms)', default=0,
        help='Runtime in milliseconds. 0 = not measured.')
    started_at = fields.Datetime('Started')

    _sql_constraints = [
        ('tool_call_dedup',
         'UNIQUE(session_id, tool_call_id)',
         'The same tool call is logged once per session.'),
    ]


class AICoworkerSessionCloseEvent(models.Model):
    """En stängning av en session (append-only historik)."""

    _name = 'ai.coworker.session.close_event'
    _description = 'Session Close Event'
    _order = 'at desc, id desc'

    session_id = fields.Many2one(
        'ai.coworker.session', string='Session', required=True,
        ondelete='cascade', index=True)
    pi_session_id = fields.Char(
        'Pi Session ID', index=True,
        help="The Pi session's UUID at closure. Stored so a closure can be "
             'correlated even if the Odoo session is later replaced.')
    reason = fields.Selection([
        ('quit', 'Quit'),
        ('reload', 'Reload'),
        ('new', 'New Session'),
        ('resume', 'Resume'),
        ('fork', 'Fork'),
        ('timeout', 'Timeout'),
        ('idle', 'Idle'),
        ('error', 'Error'),
        ('unknown', 'Unknown'),
    ], string='Reason', required=True, default='unknown', index=True,
        help='Why the session was closed. The difference from `finish_reason`: '
             'finish_reason says why the TURN ended, reason why the SESSION '
             'was closed.')
    at = fields.Datetime(
        'Closed At', required=True, default=fields.Datetime.now, index=True)

    # ⚠ INGEN UNIQUE på (session_id, reason, at). `at` har sekundupplösning,
    # så två stängningar med samma orsak inom samma sekund (t.ex. cronen och
    # klienten samtidigt, eller två snabba cykler i ett test) kolliderar och
    # fäller hela transaktionen — vilket aborterade cron-stängningen.
    # Idempotensen hanteras i `_close_from_client`/cronen med en explicit
    # sökning före create; historiken får ALDRIG tappa en stängning för att
    # två råkade hamna i samma sekund.
