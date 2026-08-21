# -*- coding: utf-8 -*-
"""user_mail_ai.mail — tunn triage-modell för mail-hjälpredan.

Livscykel: new → classified → processed | ignored.
- Ingest: _ingest_message() (IMAP-poller nu, mailgateway i Skiva 2).
- OKF-arkiv: varje mail arkiveras i personligt scope (create_from_mail).
- Graf: :MailMessage + SENT_BY registreras via data/graph_mail_node.xml.
- Klassificering: zero-shot (ProviderFactory) + deterministisk Teams-detektering.
- Teams-inbjudan → calendar.event (autonomt, låg risk) + notis.
"""

import asyncio
import base64
import json
import logging
import re
from datetime import date, datetime, timedelta, timezone
from email import policy
from email.parser import BytesParser
from email.utils import parsedate_to_datetime

import icalendar

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

try:
    from ai_agent_core.core.provider import ProviderFactory
except ImportError:  # pragma: no cover
    ProviderFactory = None

_TEAMS_KEYWORDS = (
    'invitation', 'inbjudan', 'mötesinbjudan', 'motesinbjudan',
    'meeting invitation', 'teams', 'join meeting',
)


class UserMailAiMail(models.Model):
    _name = 'user_mail_ai.mail'
    _description = 'Mail-hjälpredan: triage'
    _inherit = ['mail.thread']
    _order = 'received_at desc, id desc'

    # ── Identitet / källa ────────────────────────────────────────────
    user_id = fields.Many2one(
        'res.users', string='Ägare', required=True, ondelete='cascade',
        index=True, default=lambda self: self.env.user)
    message_id = fields.Char(
        string='Message-ID', required=True, index=True,
        help='Universell dedup-nyckel (IMAP + mailgateway).')
    raw_message_id = fields.Many2one(
        'mail.message', string='Källmeddelande', ondelete='set null',
        help='Sätts vid mailgateway-ingestion (Skiva 2).')
    object_model = fields.Char(
        'Objektmodell', index=True,
        help='Odoo-modell för källobjektet (mailgateway/promotion).')
    object_res_id = fields.Integer(
        'Objekt-id',
        help='Record-id för källobjektet (mailgateway/promotion).')
    source_uid = fields.Integer(
        'IMAP-UID', index=True,
        help='UID i källmappen — behövs för mapp-flytt/flaggor.')
    source_folder = fields.Char('Källmapp', default='INBOX')

    # ── Skiva 2: promotion / utkast / handoff / reply-zero ──
    promotion_hitl_id = fields.Many2one(
        'ai.coworker.hitl', string='Promotion-HITL', ondelete='set null')
    draft_folder = fields.Char('Utkastmapp')
    draft_uid = fields.Integer('Utkast-UID')
    reply_draft = fields.Text('Svarsutkast')
    send_hitl_id = fields.Many2one(
        'ai.coworker.hitl', string='Skick-HITL', ondelete='set null')
    handoff_coworker_id = fields.Many2one(
        'ai.coworker', string='Specialist (handoff)', ondelete='set null')
    handoff_state = fields.Selection([
        ('none', 'Ingen'),
        ('handed_off', 'Delegerad'),
        ('done', 'Klar'),
        ('failed', 'Misslyckad'),
    ], string='Handoff', default='none')
    reply_needed = fields.Boolean('Kräver svar', default=True)
    awaiting_reply = fields.Boolean('Väntar på svar', default=False)
    matched_rules = fields.Text('Matchade LLM-regler (JSON)', default='[]')
    interest_components = fields.Text('Intresse-komponenter (JSON)')
    follow_up_at = fields.Datetime('Följ upp', index=True,
                                   help='Heartbeat: förfallen uppföljning → nudge.')
    subject = fields.Char('Ämne')
    from_email = fields.Char('Avsändare (email)')
    from_name = fields.Char('Avsändare (namn)')
    received_at = fields.Datetime('Mottagen')
    folder = fields.Char('Mapp', default='INBOX')
    attachments_info = fields.Text(
        'Bilagor (JSON)', default='[]',
        help='Lista av {filename, content_type} för klassificering.')

    # ── Relationer ───────────────────────────────────────────────────
    partner_id = fields.Many2one(
        'res.partner', string='Partner', ondelete='set null', index=True)
    calendar_event_id = fields.Many2one(
        'calendar.event', string='Kalenderhändelse', ondelete='set null')

    # ── Triage / klassificering ──────────────────────────────────────
    status = fields.Selection([
        ('new', 'Ny'),
        ('classified', 'Klassificerad'),
        ('processed', 'Bearbetad'),
        ('ignored', 'Avfärdad'),
    ], string='Status', default='new', index=True)
    category = fields.Selection([
        ('newsletter', 'Nyhetsbrev'),
        ('invoice', 'Faktura'),
        ('meeting_invite', 'Mötesinbjudan'),
        ('support', 'Support'),
        ('project', 'Projekt'),
        ('personal', 'Personlig'),
        ('other', 'Övrigt'),
    ], string='Kategori')
    action_needed = fields.Boolean('Kräver åtgärd', default=False)
    interest_score = fields.Float('Intressepoäng (0–10)', default=0.0)
    teams_invite = fields.Boolean('Teams-inbjudan', default=False)
    reply_suggested = fields.Boolean('Svarsförslag', default=False)
    object_link_candidate = fields.Text(
        'Objektkandidat (JSON)',
        help='{"model": ..., "id": ..., "confidence": ...} — används i Skiva 2.')
    notes = fields.Text('Anteckningar')

    _sql_constraints = [
        ('unique_user_message',
         'unique(user_id, message_id)',
         'Message already ingested for this user!'),
    ]

    # ── Livscykel ────────────────────────────────────────────────────

    def action_mark_processed(self):
        self.write({'status': 'processed'})
        return True

    def action_mark_ignored(self):
        self.write({'status': 'ignored'})
        return True

    def action_reclassify(self):
        self.write({'status': 'new', 'notes': False})
        self._process_new_for_user(self.env.user)
        return True

    # ── Ingest ───────────────────────────────────────────────────────

    @api.model
    def _ingest_message(self, normalized_dict, user=None):
        """Gemensam ingestion för normaliserade mail.

        normalized_dict: samma form som pollerns _normalize_message
        (message_id, subject, from_, to_, date, body_text, body_html,
        attachments, folder, raw). Används av IMAP-pollen (Skiva 1) och
        mailgatewayen (Skiva 2).
        """
        user = user or self.env.user
        message_id = (normalized_dict.get('message_id') or '').strip() \
            or normalized_dict.get('dedup_key') or ''
        if not message_id:
            return self.env['user_mail_ai.mail']
        existing = self.search([
            ('user_id', '=', user.id),
            ('message_id', '=', message_id),
        ], limit=1)
        if existing:
            return existing

        from_header = normalized_dict.get('from_') or ''
        partner = self._resolve_partner(
            from_header, normalized_dict.get('from_name'))

        record = self.create({
            'user_id': user.id,
            'message_id': message_id,
            'subject': (normalized_dict.get('subject') or '')[:250],
            'from_email': self._extract_email(from_header),
            'from_name': (normalized_dict.get('from_name') or '')[:120],
            'partner_id': partner.id if partner else False,
            'received_at': self._parse_received_at(normalized_dict.get('date')),
            'folder': normalized_dict.get('folder', 'INBOX'),
            'attachments_info': json.dumps(
                normalized_dict.get('attachments') or []),
            'status': 'new',
        })

        # Rå .eml som källa till sanningen (klassificerare + teams-handler)
        raw = normalized_dict.get('raw')
        if raw:
            self.env['ir.attachment'].create({
                'name': (record.subject or 'email')[:120] + '.eml',
                'datas': base64.b64encode(raw).decode(),
                'mimetype': 'message/rfc822',
                'res_model': 'user_mail_ai.mail',
                'res_id': record.id,
            })

        # ── Skiva 2: källa + objekt + tråd-matchning ──
        extra = {
            'source_folder': normalized_dict.get('folder', 'INBOX'),
            'reply_needed': True,
            'awaiting_reply': False,
        }
        if normalized_dict.get('uid'):
            extra['source_uid'] = normalized_dict['uid']
        if normalized_dict.get('raw_message_id'):
            extra['raw_message_id'] = normalized_dict['raw_message_id']
        if normalized_dict.get('object_model') and \
                normalized_dict.get('object_res_id'):
            extra['object_model'] = normalized_dict['object_model']
            extra['object_res_id'] = normalized_dict['object_res_id']
        # Deterministisk tråd-matchning (References/In-Reply-To) — starkare
        # än LLM-kandidaten, bevaras i _classify.
        if raw and not extra.get('object_model'):
            try:
                parsed = BytesParser(policy=policy.default).parsebytes(raw)
                cand = self._find_object_by_thread(parsed)
                if cand:
                    extra['object_link_candidate'] = json.dumps(cand)
            except Exception:
                pass
        if extra:
            record.write(extra)

        # OKF-arkiv (personligt scope) — får aldrig blockera ingestion
        try:
            if 'ai.okf.concept' in self.env:
                self.env['ai.okf.concept'].create_from_mail(
                    subject=record.subject,
                    body=(normalized_dict.get('body_text')
                          or normalized_dict.get('body_html') or ''),
                    from_email=record.from_email,
                    from_name=record.from_name,
                    user=user,
                    eml_data=(base64.b64encode(raw).decode()
                              if raw else None),
                    source_ref=record.message_id,
                )
        except Exception as e:
            _logger.warning('OKF archive failed for %s: %s',
                            record.subject, e)
        return record

    @api.model
    def _ingest_mail_message(self, message):
        """Ingestera ett mail.message (mailgateway/catchall) → triage.

        Mailet har redan ett objekt (model/res_id) — object-koppling sätts
        direkt, ingen HITL-promotion behövs. Ägaren härleds ur objektet.
        """
        if not message or message.message_type != 'email':
            return False
        msg_id = message.message_id or 'msg:%s' % message.id
        existing = self.search([
            ('user_id', 'in', self.env['res.users'].search([]).ids),
            ('message_id', '=', msg_id),
        ], limit=1)
        if existing:
            return existing
        owner = self._resolve_owner(message)
        if not owner:
            # Ingen ägare (t.ex. objekt utan user_id, ingen mottagare i Odoo)
            # → ingen triage; mailet finns redan på objektets chatter.
            return False
        body_text = ''
        try:
            from odoo.tools.mail import html2plaintext
            body_text = html2plaintext(message.body or '') or ''
        except Exception:
            body_text = ''
        norm = {
            'message_id': msg_id,
            'subject': message.subject or '',
            'from_': message.email_from or '',
            'from_name': message.author_id.name or '',
            'date': str(message.date or ''),
            'body_text': body_text,
            'body_html': message.body or '',
            'attachments': [
                {'filename': a.name, 'content_type': a.mimetype}
                for a in message.attachment_ids],
            'folder': 'mailgateway',
            'raw': None,
            'raw_message_id': message.id,
            'object_model': message.model,
            'object_res_id': message.res_id,
        }
        return self._ingest_message(norm, user=owner)

    def _resolve_owner(self, message):
        """Ägare för mailgateway-mail: objektets user_id, annars mottagare."""
        if message.model and message.res_id:
            try:
                obj = self.env[message.model].browse(message.res_id)
                if 'user_id' in obj._fields and obj.user_id:
                    return obj.user_id
            except Exception:
                pass
        # Mottagarens partner → användare
        recipient = message.partner_ids[:1]
        if recipient:
            user = self.env['res.users'].search(
                [('partner_id', '=', recipient.id)], limit=1)
            if user:
                return user
        return False

    @staticmethod
    def _extract_email(from_header):
        if not from_header:
            return ''
        m = re.search(r'[\w.+-]+@[\w-]+\.[\w.]+', from_header)
        return m.group(0) if m else from_header.strip()

    def _resolve_partner(self, from_header, from_name=None):
        """Hitta/skapa res.partner från avsändaren (skill_mail_partner-mönstret)."""
        email = self._extract_email(from_header)
        if not email:
            return self.env['res.partner']
        Partner = self.env['res.partner'].sudo()
        partner = Partner.search([
            '|', ('email', '=ilike', email),
            ('email_normalized', '=ilike', email),
        ], limit=1)
        if partner:
            return partner
        name = (from_name or '').strip()
        if not name:
            m = re.match(r'^([^<]+)', from_header or '')
            name = m.group(1).strip() if m else ''
        name = re.sub(r'[<>"]', '', name).strip()
        if name:
            partner = Partner.search([
                ('name', 'ilike', name), ('is_company', '=', True),
            ], limit=1)
        if partner:
            return partner
        return Partner.create({
            'name': name or email.split('@')[0],
            'email': email,
            'is_company': False,
        })

    @staticmethod
    def _parse_received_at(date_str):
        if not date_str:
            return fields.Datetime.now()
        try:
            dt = parsedate_to_datetime(date_str)
            if dt.tzinfo:
                dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
            return dt
        except Exception:
            return fields.Datetime.now()

    # ── Pipeline ─────────────────────────────────────────────────────

    @api.model
    def _process_new_for_user(self, user):
        """Klassificera nya records → dispatch → nudge (rate-limited)."""
        records = self.search([
            ('user_id', '=', user.id), ('status', '=', 'new')])
        nudges = []
        max_drafts = self._max_drafts_per_cycle()
        drafted = 0
        for rec in records:
            rec._classify()
            if rec.status != 'classified':
                continue
            if rec.teams_invite:
                try:
                    rec._handle_teams_invite()
                except Exception as e:
                    _logger.error('Teams handler failed for %s: %s',
                                  rec.subject, e)
                    rec.write({'status': 'classified',
                               'notes': 'Teams-fel: %s' % e})
                continue
            # Regel-utvärdering (Skiva 3) — högt prioriterad regel kan
            # short-circuiter (ignore/move/draft/handoff).
            handled, nudged = rec._after_classify()
            if nudged:
                nudges.append(rec)
            if handled:
                continue
            # Nyhetsbrev → mapp (HITL första gången, sedan autonomt)
            if rec.category == 'newsletter':
                rec._maybe_move_newsletter()
                continue
            # Specialist-routing (handoff)
            if rec._try_handoff():
                continue
            # Promotion (objektkandidat) → HITL
            if rec.object_link_candidate and not rec.promotion_hitl_id:
                rec._propose_promotion()
                continue
            # Proaktivt svarsutkast (begränsat per cykel)
            if rec.reply_suggested and \
                    (rec.interest_score or 0) >= self._draft_threshold() \
                    and drafted < max_drafts:
                if rec._maybe_draft_reply():
                    drafted += 1
            # Flagga + nudge
            rec._flag_action_mail()
            if rec.action_needed or \
                    (rec.interest_score or 0) >= self._nudge_threshold():
                nudges.append(rec)
        if nudges:
            self._nudge_user(user, nudges)
        return len(records)

    @api.model
    def _draft_threshold(self):
        param = self.env['ir.config_parameter'].sudo().get_param(
            'user_mail_ai.draft_threshold', '6.0')
        try:
            return float(param)
        except Exception:
            return 6.0

    @api.model
    def _max_drafts_per_cycle(self):
        param = self.env['ir.config_parameter'].sudo().get_param(
            'user_mail_ai.max_drafts_per_cycle', '5')
        try:
            return int(param)
        except Exception:
            return 5

    @api.model
    def _nudge_threshold(self):
        param = self.env['ir.config_parameter'].sudo().get_param(
            'user_mail_ai.nudge_threshold', '7.0')
        try:
            return float(param)
        except Exception:
            return 7.0

    # ── Klassificering ───────────────────────────────────────────────

    def _assistant(self):
        """Användarens hjälpreda (ai_coworker_id) eller default Mail-hjälpredan."""
        user = self.env.user
        if user.ai_coworker_id:
            return user.ai_coworker_id
        default = self.env.ref(
            'user_mail_ai.coworker_mail_assistant', raise_if_not_found=False)
        if default:
            return default
        return self.env['ai.coworker'].search(
            [('name', 'ilike', 'Mail-hjälpredan')], limit=1)

    def _get_body_text(self, max_chars=3000):
        """Body-text ur råa .eml-bilagan (triage lagrar inte body)."""
        att = self.env['ir.attachment'].search([
            ('res_model', '=', 'user_mail_ai.mail'),
            ('res_id', '=', self.id),
        ], limit=1)
        if not att or not att.datas:
            return ''
        try:
            msg = BytesParser(policy=policy.default).parsebytes(
                base64.b64decode(att.datas))
        except Exception:
            return ''
        texts = []
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == 'text/plain':
                    try:
                        texts.append(part.get_content() or '')
                    except Exception:
                        pass
        else:
            try:
                texts.append(msg.get_content() or '')
            except Exception:
                pass
        return '\n'.join(texts)[:max_chars]

    def _detect_teams_invite(self):
        """Deterministisk Teams-detektering: .ics/text-calendar + nyckelord."""
        subj = (self.subject or '').lower()
        if any(k in subj for k in _TEAMS_KEYWORDS):
            return True
        try:
            atts = json.loads(self.attachments_info or '[]')
        except Exception:
            atts = []
        for a in atts:
            fn = (a.get('filename') or '').lower()
            ct = (a.get('content_type') or '').lower()
            if fn.endswith('.ics') or ct == 'text/calendar':
                return True
        return False

    def _build_classification_prompt(self):
        return (
            "Du klassificerar ett inkommande mail för en personlig "
            "mail-hjälpreda. Svara ENDAST med JSON, ingen annan text:\n"
            '{\n'
            '  "category": "newsletter|invoice|meeting_invite|support|'
            'project|personal|other",\n'
            '  "action_needed": true|false,\n'
            '  "interest_score": 0-10,\n'
            '  "teams_invite": true|false,\n'
            '  "object_link_candidate": null eller '
            '{"model": "...", "id": 123, "confidence": 0.0-1.0},\n'
            '  "reply_suggested": true|false\n'
            '}\n\n'
            'Mail:\n'
            'Från: %s\n'
            'Ämne: %s\n'
            'Datum: %s\n'
            'Bilagor: %s\n'
            'Innehåll:\n%s'
        ) % (
            self.from_email or self.from_name or '?',
            self.subject or '',
            self.received_at or '',
            self.attachments_info or '[]',
            self._get_body_text(),
        )

    @staticmethod
    def _parse_classification_json(text):
        if not text:
            return {}
        text = text.strip()
        try:
            return json.loads(text)
        except Exception:
            m = re.search(r'\{.*\}', text, re.DOTALL)
            if m:
                try:
                    return json.loads(m.group(0))
                except Exception:
                    pass
        return {}

    def _classify(self):
        """Zero-shot-klassificering (en LLM-call) + deterministisk Teams-detektering."""
        self.ensure_one()
        if self.status != 'new':
            return False
        teams_det = self._detect_teams_invite()

        data = {}
        provider = model = None
        if ProviderFactory is not None:
            try:
                provider, model = ProviderFactory.from_coworker(
                    self._assistant())
            except Exception as e:
                _logger.warning('Provider resolution failed: %s', e)
        if not provider or not model:
            # Ingen provider → spara deterministisk detektering, försök igen nästa cykel
            if teams_det:
                self.write({'teams_invite': True, 'status': 'classified'})
                return True
            self.write({'notes': 'Ingen provider/modell konfigurerad '
                                 '(ai_agent_core.default_model_id).'})
            return False

        model_name = model.api_name or model.name
        try:
            resp = asyncio.run(provider.chat(
                model_name,
                [{'role': 'user', 'content': self._build_classification_prompt()}],
                temperature=0.0,
                max_tokens=600,
            ))
            data = self._parse_classification_json(resp.text)
        except Exception as e:
            _logger.error('Classification call failed for %s: %s',
                          self.subject, e)
            self.write({'notes': 'Klassificering misslyckades: %s' % e})
            return False

        try:
            category = data.get('category')
            valid_categories = [c[0] for c in self._fields['category'].selection]
            if category not in valid_categories:
                category = 'other'
            teams_llm = bool(data.get('teams_invite'))
            object_candidate = data.get('object_link_candidate') or None
            self.write({
                'category': category,
                'action_needed': bool(data.get('action_needed')),
                'interest_score': float(data.get('interest_score') or 0.0),
                'teams_invite': bool(teams_det or teams_llm),
                'object_link_candidate': (
                    json.dumps(object_candidate) if object_candidate else False),
                'reply_suggested': bool(data.get('reply_suggested')),
                'matched_rules': json.dumps(
                    data.get('matched_rules') or []),
                'status': 'classified',
            })
            return True
        except Exception as e:
            _logger.error('Classification store failed for %s: %s',
                          self.subject, e)
            self.write({'notes': 'Klassificering misslyckades: %s' % e})
            return False
        # Deterministisk tråd-matchning vinner över LLM-kandidaten.
        for rec in self:
            cand = rec._thread_candidate()
            if cand:
                rec.write({'object_link_candidate': json.dumps(cand)})
        return True

    # ── Teams-inbjudan → calendar.event ──────────────────────────────

    def _parse_raw_mail(self):
        """Parsa råa .eml-bilagan → (msg, body_text)."""
        att = self.env['ir.attachment'].search([
            ('res_model', '=', 'user_mail_ai.mail'),
            ('res_id', '=', self.id),
        ], limit=1)
        if not att or not att.datas:
            return None, ''
        try:
            msg = BytesParser(policy=policy.default).parsebytes(
                base64.b64decode(att.datas))
        except Exception:
            return None, ''
        body_text = ''
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == 'text/plain':
                    try:
                        body_text = part.get_content() or ''
                        break
                    except Exception:
                        pass
        else:
            try:
                body_text = msg.get_content() or ''
            except Exception:
                pass
        return msg, body_text

    def _extract_invite_fallback(self, body_text):
        """Regex-fallback: datum/tid + Teams-URL när .ics saknas."""
        summary = self.subject or 'Möte'
        desc = ''
        m = re.search(r'https://teams\.microsoft\.com/\S+', body_text or '')
        if m:
            desc = m.group(0)
        # ISO-datum: 2026-08-10 14:00 eller 2026-08-10T14:00
        m = re.search(
            r'(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})', body_text or '')
        if not m:
            m = re.search(
                r'(\d{2})/(\d{2})/(\d{4})[ ,]?(\d{2}):(\d{2})', body_text or '')
        if not m:
            return None, None, summary, desc, None
        if len(m.groups()) == 5 and m.group(3).isdigit() and int(m.group(3)) > 31:
            # YYYY-MM-DD HH:MM
            start = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)),
                             int(m.group(4)), int(m.group(5)))
        else:
            # MM/DD/YYYY HH:MM
            start = datetime(int(m.group(3)), int(m.group(1)), int(m.group(2)),
                             int(m.group(4)), int(m.group(5)))
        return start, start + timedelta(hours=1), summary, desc, None

    @staticmethod
    def _to_naive_dt(value):
        if isinstance(value, datetime):
            if value.tzinfo:
                return value.astimezone(timezone.utc).replace(tzinfo=None)
            return value
        if isinstance(value, date):
            return datetime.combine(value, datetime.min.time())
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(
                    value.replace('Z', '+00:00')
                ).astimezone(timezone.utc).replace(tzinfo=None)
            except Exception:
                return None
        return None

    def _handle_teams_invite(self):
        """Skapa calendar.event från inbjudan (.ics → fallback regex)."""
        self.ensure_one()
        if self.calendar_event_id:
            return self.calendar_event_id
        if not self.teams_invite:
            return False

        msg, body_text = self._parse_raw_mail()
        start = stop = summary = desc = organizer_email = None
        if msg is not None:
            for part in msg.walk():
                fn = part.get_filename() or ''
                if fn.lower().endswith('.ics') or \
                        part.get_content_type() == 'text/calendar':
                    try:
                        cal = icalendar.Calendar.from_ical(part.get_content())
                    except Exception as e:
                        _logger.warning('ICS parse failed for %s: %s',
                                        self.subject, e)
                        break
                    for comp in cal.walk():
                        if comp.name == 'VEVENT':
                            summary = str(comp.get('summary')
                                          or self.subject or 'Möte')
                            desc = str(comp.get('description') or '')
                            dtstart = comp.get('dtstart')
                            dtend = comp.get('dtend')
                            if dtstart is not None:
                                start = dtstart.dt
                            if dtend is not None:
                                stop = dtend.dt
                            org = comp.get('organizer')
                            if org:
                                organizer_email = str(org).replace('mailto:', '')
                    break

        if start is None:
            start, stop, summary, desc, organizer_email = \
                self._extract_invite_fallback(body_text)
        if start is None:
            self.write({'status': 'classified',
                        'notes': 'Kunde inte tolka inbjudan.'})
            return False

        start_dt = self._to_naive_dt(start)
        stop_dt = self._to_naive_dt(stop) if stop else (
            start_dt + timedelta(hours=1))
        if start_dt is None:
            self.write({'status': 'classified',
                        'notes': 'Kunde inte tolka starttid.'})
            return False

        event = self.env['calendar.event'].with_user(self.user_id).create({
            'name': (summary or self.subject or 'Möte')[:200],
            'start': start_dt,
            'stop': stop_dt,
            'user_id': self.user_id.id,
            'description': desc or '',
        })
        if organizer_email:
            partner = self._resolve_partner(organizer_email)
            if partner:
                event.write({'partner_ids': [(4, partner.id)]})

        # Chatter + notis på eventet
        event.message_post(
            body='Skapad av Mail-hjälpredan från mail: <b>%s</b> (%s)'
                 % (self.subject or '', self.from_email or '?'),
            message_type='comment')
        event.message_post(
            body='Mail-hjälpredan lade in din mötesinbjudan i kalendern.',
            message_type='notification',
            partner_ids=[self.user_id.partner_id.id])
        self.write({'calendar_event_id': event.id, 'status': 'processed'})
        return event

    # ── Nudge ────────────────────────────────────────────────────────

    def _after_classify(self):
        """Hook efter klassificering — Skiva 3 (regler) överlagrar.

        Returns (handled, nudged): handled=True short-circuiter
        default-pipelinen; nudged=True lägger till i nudges.
        """
        return False, False

    def _nudge_user(self, user, records):
        """En notis per cykel (rate-limit) + Discuss-DM från bot (best-effort)."""
        if not records:
            return
        lines = []
        cat_map = dict(self._fields['category'].selection)
        for r in records[:5]:
            lines.append(
                '- <b>%s</b> (%s) — %s'
                % (r.subject or '(ingen rubrik)', r.from_email or '?',
                   cat_map.get(r.category, r.category or 'oklassad')))
        body = ('Mail-hjälpredan: %d mail väntar på uppmärksamhet.<br/>%s'
                % (len(records), '<br/>'.join(lines)))
        # Notis postas på den första triage-posten (self kan vara tom i
        # @api.model-kontext — message_post kräver ett record med mail.thread).
        primary = records[0]
        try:
            primary.sudo().message_post(
                body=body, message_type='notification',
                partner_ids=[user.partner_id.id])
        except Exception as e:
            _logger.warning('Nudge notification failed: %s', e)
        try:
            self._discuss_dm(user, body)
        except Exception as e:
            _logger.warning('Nudge discuss DM failed: %s', e)

    def _discuss_dm(self, user, body):
        """Privat Discuss-meddelande från hjälpredans bot-användare."""
        coworker = self._assistant()
        if not coworker:
            return
        chat_it = coworker.init_type_ids.filtered(
            lambda it: it.init_type == 'chat' and it.enabled)[:1]
        if not chat_it:
            return
        if not chat_it.chat_user_id:
            chat_it._ensure_chat_user()
        bot_user = chat_it.chat_user_id
        if not bot_user:
            return
        channel = self.env['discuss.channel'].sudo().search([
            ('channel_type', '=', 'chat'),
            ('channel_partner_ids', 'in', [bot_user.partner_id.id]),
            ('channel_partner_ids', 'in', [user.partner_id.id]),
        ], limit=1)
        if not channel:
            channel = self.env['discuss.channel'].sudo().create({
                'name': 'Mail-hjälpredan',
                'channel_type': 'chat',
                'channel_partner_ids': [
                    (6, 0, [bot_user.partner_id.id, user.partner_id.id])],
            })
        channel.sudo().message_post(
            body=body, message_type='comment',
            author_id=bot_user.partner_id.id)

    # ══════════════════════════════════════════════════════════════
    # SKIVA 2 — interaktion: promotion, utkast, mappar, routing
    # ══════════════════════════════════════════════════════════════

    # ── Tråd-matchning (deterministisk objektkoppling) ──────────────

    def _find_object_by_thread(self, parsed_msg):
        """References/In-Reply-To → mail.message.message_id → (model, res_id)."""
        refs = []
        for header in ('References', 'In-Reply-To'):
            val = parsed_msg.get(header) or ''
            refs += re.findall(r'<[^>]+>', val)
        for ref in refs:
            m = self.env['mail.message'].search(
                [('message_id', '=', ref)], limit=1)
            if m and m.model and m.res_id:
                return {
                    'model': m.model, 'res_id': m.res_id,
                    'confidence': 1.0, 'source': 'thread',
                }
        return None

    def _thread_candidate(self):
        """Återberäkna tråd-kandidaten från råa eml (för _classify-överlagring)."""
        att = self.env['ir.attachment'].search([
            ('res_model', '=', 'user_mail_ai.mail'),
            ('res_id', '=', self.id)], limit=1)
        if not att or not att.datas:
            return None
        try:
            parsed = BytesParser(policy=policy.default).parsebytes(
                base64.b64decode(att.datas))
        except Exception:
            return None
        return self._find_object_by_thread(parsed)

    # ── Promotion (mail → objekt, HITL-gat) ─────────────────────────

    def _propose_promotion(self):
        """Skapa HITL-request: koppla in mail i objektet (synligt för följare)."""
        self.ensure_one()
        if self.promotion_hitl_id and \
                self.promotion_hitl_id.state == 'asked':
            return self.promotion_hitl_id
        try:
            cand = json.loads(self.object_link_candidate or 'null')
        except Exception:
            cand = None
        if not cand or not cand.get('model') or not cand.get('res_id'):
            return False
        coworker = self._assistant()
        if not coworker:
            return False
        hitl = coworker._request_hitl(
            'promote_mail',
            'Koppla in mail "%s" från %s i %s #%s? '
            '(synligt för alla följare)'
            % (self.subject or '', self.from_email or '?',
               cand.get('model'), cand.get('res_id')),
            context={
                'model': cand.get('model'), 'res_id': cand.get('res_id'),
                'mail_id': self.id,
                'signal': cand.get('source', 'llm'),
                'confidence': cand.get('confidence', 0.0),
            },
            risk_level='high',
            user_id=self.user_id.id,
        )
        self.write({'promotion_hitl_id': hitl.id})
        return hitl

    def _do_promotion(self):
        """Godkänd promotion → mail.message på objektets chatter (utan att skicka)."""
        self.ensure_one()
        try:
            cand = json.loads(self.object_link_candidate or 'null')
        except Exception:
            cand = None
        if not cand or not cand.get('model') or not cand.get('res_id'):
            self.write({'status': 'classified',
                        'notes': 'Promotion saknade objekt.'})
            return False
        msg, body_text = self._parse_raw_mail()
        body_html = ''
        if msg is not None:
            for part in msg.walk():
                if part.get_content_type() == 'text/html':
                    try:
                        body_html = part.get_content() or ''
                        break
                    except Exception:
                        pass
        body_html = body_html or (
            '<p>%s</p>' % (body_text or '').replace('\n', '<br/>'))
        message = self.env['mail.message'].sudo().create({
            'model': cand['model'],
            'res_id': cand['res_id'],
            'body': body_html,
            'author_id': self.partner_id.id if self.partner_id else False,
            'email_from': self.from_email or False,
            'message_type': 'email',
            'subtype_id': self.env.ref(
                'mail.mt_comment', raise_if_not_found=False).id or False,
            'message_id': self.message_id or False,
        })
        # Bilagor ur råa eml → ir.attachment på meddelandet
        att_ids = []
        if msg is not None:
            for part in msg.walk():
                fn = part.get_filename()
                if not fn:
                    continue
                payload = part.get_payload(decode=True)
                if not payload:
                    continue
                att = self.env['ir.attachment'].sudo().create({
                    'name': fn,
                    'datas': base64.b64encode(payload).decode(),
                    'mimetype': part.get_content_type()
                    or 'application/octet-stream',
                    'res_model': 'mail.message',
                    'res_id': message.id,
                })
                att_ids.append(att.id)
        if att_ids:
            message.write({'attachment_ids': [(6, 0, att_ids)]})
        self.write({
            'status': 'processed',
            'object_model': cand['model'],
            'object_res_id': cand['res_id'],
            'notes': 'Promoterad till %s #%s'
                     % (cand['model'], cand['res_id']),
        })
        return True

    # ── Svarsutkast (proaktivt, IMAP-Drafts) ────────────────────────

    def _maybe_draft_reply(self):
        """Skapa svarsutkast i användarens IMAP-Drafts (om inget finns)."""
        self.ensure_one()
        if self.draft_uid:
            return False
        provider = model = None
        if ProviderFactory is not None:
            try:
                provider, model = ProviderFactory.from_coworker(
                    self._assistant())
            except Exception:
                provider = model = None
        if not provider or not model:
            return False
        model_name = model.api_name or model.name
        prompt = (
            'Skriv ett kort, professionellt svarsutkast på detta mail. '
            'Svara ENDAST med själva svaret — ingen inledning, ingen '
            'signering.\n\nFrån: %s\nÄmne: %s\n\n%s'
            % (self.from_email or '?', self.subject or '',
               self._get_body_text(3000)))
        try:
            resp = asyncio.run(provider.chat(
                model_name,
                [{'role': 'user', 'content': prompt}],
                temperature=0.4, max_tokens=800,
            ))
        except Exception as e:
            _logger.error('Draft generation failed for %s: %s',
                          self.subject, e)
            return False
        reply_text = (resp.text or '').strip()
        if not reply_text:
            return False
        imap = self.env['user.mail.imap'].with_user(self.user_id)
        try:
            res = imap.action_append_draft(
                self.from_email or '',
                'Re: %s' % (self.subject or ''),
                reply_text,
                in_reply_to=self.message_id or False,
            )
        except Exception as e:
            _logger.error('Draft append failed for %s: %s', self.subject, e)
            return False
        self.write({
            'draft_folder': res.get('folder', 'Drafts'),
            'draft_uid': res.get('uid') or False,
            'reply_draft': reply_text,
            'notes': 'Svarsutkast skapat i %s.' % res.get('folder', 'Drafts'),
        })
        return True

    def action_suggest_send(self):
        """HITL: skicka utkastet via användarens SMTP-credentials."""
        self.ensure_one()
        if not self.draft_uid and not self.reply_draft:
            return False
        if self.send_hitl_id and self.send_hitl_id.state == 'asked':
            return self.send_hitl_id
        coworker = self._assistant()
        if not coworker:
            return False
        hitl = coworker._request_hitl(
            'send_reply',
            'Skicka svarsutkastet till %s ("%s")?'
            % (self.from_email or '?', self.subject or ''),
            context={
                'mail_id': self.id,
                'draft_folder': self.draft_folder or 'Drafts',
                'draft_uid': self.draft_uid or 0,
            },
            risk_level='high',
            user_id=self.user_id.id,
        )
        self.write({'send_hitl_id': hitl.id})
        return hitl

    def _do_send_reply(self, hitl=None):
        """Godkänt skick: hämta utkastet → SMTP (användarens konto)."""
        self.ensure_one()
        folder = self.draft_folder or 'Drafts'
        uid = self.draft_uid
        if hitl:
            try:
                ctx = json.loads(hitl.context or '{}')
            except Exception:
                ctx = {}
            folder = ctx.get('draft_folder') or folder
            uid = ctx.get('draft_uid') or uid
        if not uid:
            # Inget IMAP-utkast — använd genererat utkast-text
            if self.reply_draft:
                imap = self.env['user.mail.imap'].with_user(self.user_id)
                imap.action_send_mail(
                    self.from_email or '', 'Re: %s' % (self.subject or ''),
                    self.reply_draft)
                self.write({'status': 'processed', 'reply_needed': False,
                            'awaiting_reply': True,
                            'notes': 'Svar skickat (genererat utkast).'})
                return True
            return False
        imap = self.env['user.mail.imap'].with_user(self.user_id)
        raw = imap.action_fetch_draft_raw(folder, uid)
        if not raw:
            self.write({'notes': 'Kunde inte hämta utkastet för skick.'})
            return False
        parsed = BytesParser(policy=policy.default).parsebytes(raw)
        to = str(parsed.get('To', self.from_email or ''))
        subject = str(parsed.get('Subject', ''))
        body = ''
        if parsed.is_multipart():
            for part in parsed.walk():
                if part.get_content_type() == 'text/plain':
                    try:
                        body = part.get_content() or ''
                        break
                    except Exception:
                        pass
        else:
            try:
                body = parsed.get_content() or ''
            except Exception:
                body = ''
        imap.action_send_mail(to, subject, body)
        self.write({'status': 'processed', 'reply_needed': False,
                    'awaiting_reply': True,
                    'notes': 'Svar skickat via HITL.'})
        return True

    def _do_reply_in_thread(self, hitl=None):
        """Catchall-mail: svara Odoo-nativt i tråden (message_post, parent_id)."""
        self.ensure_one()
        if not self.raw_message_id or not self.object_model \
                or not self.object_res_id:
            return False
        reply_text = self.reply_draft
        if not reply_text:
            provider = model = None
            if ProviderFactory is not None:
                try:
                    provider, model = ProviderFactory.from_coworker(
                        self._assistant())
                except Exception:
                    provider = model = None
            if provider and model:
                try:
                    resp = asyncio.run(provider.chat(
                        model.api_name or model.name,
                        [{'role': 'user', 'content': (
                            'Skriv ett kort, professionellt svar på detta '
                            'mail. Svara ENDAST med själva svaret.\n\n'
                            'Från: %s\nÄmne: %s\n\n%s'
                            % (self.from_email or '?', self.subject or '',
                               self._get_body_text(3000)))}],
                        temperature=0.4, max_tokens=800))
                    reply_text = (resp.text or '').strip()
                except Exception:
                    reply_text = ''
        if not reply_text:
            return False
        obj = self.env[self.object_model].browse(self.object_res_id)
        obj.with_user(self.user_id).message_post(
            body='<p>%s</p>' % reply_text.replace('\n', '<br/>'),
            subject='Re: %s' % (self.subject or ''),
            parent_id=self.raw_message_id.id,
            message_type='comment',
            subtype_xmlid='mail.mt_comment',
        )
        self.write({'status': 'processed', 'reply_needed': False,
                    'awaiting_reply': True,
                    'notes': 'Svar postat i tråden (%s #%s).'
                             % (self.object_model, self.object_res_id)})
        return True

    def action_reply_in_thread(self):
        """Knapp (catchall-mail): skapa HITL för svar i Odoo-tråden."""
        self.ensure_one()
        if self.send_hitl_id and self.send_hitl_id.state == 'asked':
            return self.send_hitl_id
        coworker = self._assistant()
        if not coworker:
            return False
        hitl = coworker._request_hitl(
            'send_reply',
            'Svara i tråden på %s #%s ("%s")?'
            % (self.object_model or '?', self.object_res_id or '?',
               self.subject or ''),
            context={'mail_id': self.id, 'in_thread': True},
            risk_level='high',
            user_id=self.user_id.id,
        )
        self.write({'send_hitl_id': hitl.id})
        return hitl

    # ── Mappar & flaggor ────────────────────────────────────────────

    def _maybe_move_newsletter(self):
        """Nyhetsbrev: första gången HITL (standing-rule-fråga), sedan autonomt."""
        self.ensure_one()
        user = self.user_id
        if not user.ai_newsletter_move_enabled:
            existing = self.env['ai.coworker.hitl'].search([
                ('user_id', '=', user.id),
                ('action_type', '=', 'newsletter_move_rule'),
                ('state', '=', 'asked'),
            ], limit=1)
            if not existing:
                coworker = self._assistant()
                if coworker:
                    coworker._request_hitl(
                        'newsletter_move_rule',
                        'Flytta nyhetsbrev till AI/Newsletters automatiskt? '
                        '(t.ex. "%s" från %s)'
                        % (self.subject or '', self.from_email or '?'),
                        context={'model': self._name, 'res_id': self.id},
                        risk_level='safe',
                        user_id=user.id,
                    )
            return False
        return self._move_to_ai_newsletters()

    def _move_to_ai_newsletters(self):
        """Flytta till AI/Newsletters (loggat, reversibelt)."""
        self.ensure_one()
        if not self.source_uid:
            return False
        imap = self.env['user.mail.imap'].with_user(self.user_id)
        try:
            imap.action_ensure_folder('AI/Newsletters')
            imap.action_move(self.source_folder or 'INBOX',
                             [self.source_uid], 'AI/Newsletters')
        except Exception as e:
            _logger.warning('Newsletter move failed for %s: %s',
                            self.subject, e)
            return False
        self.write({
            'folder': 'AI/Newsletters',
            'status': 'processed',
            'notes': 'Flyttad till AI/Newsletters.',
        })
        return True

    def action_move_back(self):
        """Reversibilitet: flytta tillbaka till INBOX."""
        self.ensure_one()
        if not self.source_uid:
            return False
        imap = self.env['user.mail.imap'].with_user(self.user_id)
        try:
            imap.action_move('AI/Newsletters', [self.source_uid], 'INBOX')
        except Exception as e:
            _logger.warning('Move back failed for %s: %s', self.subject, e)
            return False
        self.write({'folder': 'INBOX', 'status': 'classified',
                    'notes': 'Flyttad tillbaka till INBOX.'})
        return True

    def _flag_action_mail(self):
        """\\Flagged för action-mail (synlig i alla klienter)."""
        self.ensure_one()
        if not self.source_uid:
            return False
        important = self.action_needed or \
            (self.interest_score or 0) >= self._nudge_threshold()
        if not important:
            return False
        imap = self.env['user.mail.imap'].with_user(self.user_id)
        try:
            imap.action_set_flag(self.source_folder or 'INBOX',
                                 [self.source_uid], 'flagged', True)
        except Exception as e:
            _logger.warning('Flag failed for %s: %s', self.subject, e)
            return False
        return True

    # ── Specialist-routing (handoff) ────────────────────────────────

    def _try_handoff(self):
        """Delegera till aktiv specialist-coworker (data-driven routing)."""
        self.ensure_one()
        if not self.category:
            return False
        routing = self.env['user_mail_ai.routing'].search([
            ('category', '=', self.category),
            ('active', '=', True),
        ], limit=1)
        if not routing or not routing.coworker_id:
            return False
        specialist = routing.coworker_id
        if specialist.status != 'active':
            return False
        try:
            prompt = (
                'Behandla detta mail: kategori=%s, från=%s, ämne=%s.\n\n%s'
                % (self.category, self.from_email or '?',
                   self.subject or '', self._get_body_text(4000)))
            specialist.with_user(self.user_id).run(prompt=prompt)
            self.write({'handoff_coworker_id': specialist.id,
                        'handoff_state': 'handed_off'})
            return True
        except Exception as e:
            _logger.error('Handoff failed for %s: %s', self.subject, e)
            self.write({'handoff_state': 'failed',
                        'notes': 'Handoff misslyckades: %s' % e})
            return False
