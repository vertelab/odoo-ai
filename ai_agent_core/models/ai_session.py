# -*- coding: utf-8 -*-
"""ai.coworker.session — standalone session model for agent runs."""

import json, logging, uuid
from datetime import timedelta
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

    # Mail-svar med dröjsmål (mail-trigger): svarstexten postas av cron
    # när reply_at passerats.
    pending_reply = fields.Text('Pendande mail-svar',
        help='Svarstext som postas av cron efter svarsdröjsmålet.')
    reply_at = fields.Datetime('Svara efter',
        help='Tidpunkt då det fördröjda svaret ska postas.')

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
        (alias@företagets-domän). alias_defaults sätter coworker_id
        (= ai_agent). Dispatcher baserat på mail-init:ens mail_action:
        - reply: kör medarbetaren på body (ev. med dröjsmål)
        - create_record: skapar/uppdaterar målmodellen
        - invoice_ai: leverantörsfaktura-flöde (partner + OCR + account.move)
        Svaret postas på sessionstråden.
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

        mail_its = coworker.init_type_ids.filtered(
            lambda it: it.init_type == 'mail' and it.enabled)
        mail_it = (mail_its.filtered(lambda it: it.mail_action != 'reply')[:1]
                   or mail_its[:1])
        action = mail_it.mail_action if mail_it else 'reply'
        delay = mail_it.mail_reply_delay if mail_it else 0

        try:
            if action in ('invoice_ai', 'process'):
                reply_text = session._process_mail_generic(
                    coworker, msg_dict)
            elif action == 'create_record':
                reply_text = session._process_create_record(
                    coworker, msg_dict, mail_it)
            else:
                prompt = f"{subject}\n\n{body}".strip()
                reply = coworker.with_context(
                    _ai_context_model='ai.coworker.session',
                    _ai_context_id=session.id,
                    _ai_auto_approve=True).run(prompt, session=session)
                reply_text = reply or 'Klart — inget svar genererades.'

            if delay and delay > 0:
                session.pending_reply = reply_text
                session.reply_at = fields.Datetime.now() + timedelta(
                    minutes=delay)
                _logger.info('Mail-svar fördröjt %s min för session %s',
                             delay, session.id)
            else:
                session.message_post(
                    body=reply_text,
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
        """Hitta res.partner från avsändaren (deterministiskt, via email).

        Hittas ingen returneras False — LLM:en skapar partnern via
        skill:en 'Mail: Hitta/skapa res.partner' under körningen.
        """
        email, _name = self._sender_from_msg(msg_dict)
        if not email:
            return self.env['res.partner']
        return self.env['res.partner'].search(
            ['|', ('email', '=', email),
             ('email_normalized', '=', email.lower())],
            limit=1)

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
            'config_json': self.config_json,
            'user_id': self.user_id.id,
            'resumed_from_id': self.id,
        })
        _logger.info('Resumed session %s from %s', new_session.name, self.name)
        return new_session
