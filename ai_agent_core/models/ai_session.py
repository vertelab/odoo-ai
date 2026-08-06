# -*- coding: utf-8 -*-
"""ai.coworker.session — standalone session model for agent runs."""

import json, logging, uuid
from odoo import models, fields, api

_logger = logging.getLogger(__name__)


class AICoworkerSession(models.Model):
    _name = 'ai.coworker.session'
    _inherit = ['mail.thread']
    _description = 'AI Quest Session'
    _order = 'create_date desc'

    task_id = fields.Many2one('ai.org.task', string='Task',
        help='Tasken som denna session arbetar på. Skapas automatiskt vid checkout.')

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

    token_input = fields.Integer('Input Tokens', default=0)
    token_output = fields.Integer('Output Tokens', default=0)
    cost_estimated = fields.Float('Cost (USD)', default=0.0)

    create_date = fields.Datetime('Started', default=lambda self: fields.Datetime.now())
    end_date = fields.Datetime('Ended')
    round_count = fields.Integer('Rounds', default=0)
    finish_reason = fields.Char('Finish Reason')

    user_id = fields.Many2one('res.users', default=lambda self: self.env.user)
    company_id = fields.Many2one('res.company', default=lambda self: self.env.company)

    # Thread support
    thread_name = fields.Char('Thread Name')
    memory_ids = fields.One2many('ai.memory', 'session_id', string='Session Memories',
        help='Uploaded documents and FAISS memories for this session')
    session_line_ids = fields.One2many(
        'ai.coworker.session.line', 'session_id', string='Messages')
    line_count = fields.Integer('Messages', compute='_compute_line_count')
    active = fields.Boolean('Active', default=True)

    @api.depends('session_line_ids')
    def _compute_line_count(self):
        for r in self:
            r.line_count = len(r.session_line_ids)

    def action_get_lines(self):
        return {
            'name': 'Messages', 'type': 'ir.actions.act_window',
            'res_model': 'ai.coworker.session.line', 'view_mode': 'list,form',
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

        # Look up sys_multiplier from ai.model
        sys_mult = 1.0
        if model_real:
            ai_model = self.env['ai.model'].search(
                [('name', 'ilike', model_real)], limit=1)
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

    def mark_done(self, reason='stop'):
        self.status = 'done'
        self.finish_reason = reason
        self.end_date = fields.Datetime.now()

    # ── Durable Resume (OpenWorker-inspired) ──
    resumable = fields.Boolean('Resumable', default=True,
                                help='Can this session be resumed after interruption?')
    resumed_from_id = fields.Many2one('ai.coworker.session', string='Resumed From',
                                       help='Parent session this was resumed from')

    def mark_interrupted(self):
        """Mark session as interrupted (crash/stop) but resumable."""
        self.status = 'active'  # Keep active so it can be resumed
        self.finish_reason = 'interrupted'
        self.end_date = fields.Datetime.now()
        _logger.info('Session %s marked interrupted (resumable)', self.name)

    @api.model
    def message_new(self, msg_dict, custom_values=None):
        """Inkommande mail → skapa session + kör medarbetaren på mailinnehållet.

        Mailgateway anropar detta när mail anländer till aliaset
        (alias@företagets-domän). alias_defaults sätter ai_coworker_id
        (= ai_agent). Svaret postas som meddelande på sessionstråden.
        """
        defaults = dict(custom_values or {})
        # Stödjer både nytt (coworker_id) och gammalt (ai_coworker_id) alias-default
        coworker_id = (defaults.get('coworker_id')
                       or defaults.get('ai_coworker_id'))
        coworker = self.env['ai.coworker'].browse(coworker_id)
        subject = msg_dict.get('subject') or 'Inkommande mail'
        body = msg_dict.get('body') or ''
        # Mailgateway förväntar sig att message_new skapar recordet
        session = self.with_context(mail_create_nosubscribe=True).create(defaults)
        if not coworker:
            session.name = subject
            return session
        # Kör medarbetaren på mailinnehållet (återanvänd sessionen)
        try:
            prompt = f"{subject}\n\n{body}".strip()
            reply = coworker.with_context(
                _ai_context_model='ai.coworker.session',
                _ai_context_id=session.id).run(prompt, session=session)
            session.message_post(
                body=reply or 'Klart — inget svar genererades.',
                subtype_xmlid='mail.mt_comment',
                message_type='comment',
            )
        except Exception as e:
            _logger.warning('mail-bearbetning misslyckades för session %s: %s',
                            session.id, e)
            session.message_post(
                body=f'Fel vid bearbetning av mailet: {e}',
                subtype_xmlid='mail.mt_comment',
                message_type='comment',
            )
        return session

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
            'config_json': self.config_json,
            'user_id': self.user_id.id,
            'resumed_from_id': self.id,
        })
        _logger.info('Resumed session %s from %s', new_session.name, self.name)
        return new_session
