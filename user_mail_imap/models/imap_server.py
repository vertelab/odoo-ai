from odoo import models, api, fields, _
from odoo.exceptions import UserError
from email import policy
from email.parser import BytesParser
from email.message import EmailMessage
import email.utils
import hashlib
import imaplib
import smtplib
import time
import odoo.tools
import logging

import re

_logger = logging.getLogger(__name__)

# Simple mail detail cache
_mail_cache = {}


class ImapServer(models.AbstractModel):
    _name = 'user.mail.imap'
    _description = 'IMAP Mail Operations'

    def _get_config(self, key, default=None):
        return odoo.tools.config.get(key, default)

    def _decrypt_password(self):
        user = self.env.user
        if not user.imap_password:
            return None
        return user._decrypt_imap_pw()

    def _connect_imap(self):
        password = self._decrypt_password()
        if not password:
            raise UserError(_("No IMAP password configured. Use user settings to set it."))
        host = self._get_config('imap_host', 'localhost')
        port = int(self._get_config('imap_port', 993))
        user = self.env.user.postfix_mail
        if not user:
            raise UserError(_("No postfix mail configured for this user."))
        conn = imaplib.IMAP4_SSL(host, port)
        conn.login(user, password)
        return conn

    def action_list_folders(self):
        conn = self._connect_imap()
        try:
            result = []
            for item in conn.list()[1]:
                decoded = item.decode('utf-8')
                _logger.info('IMAP FOLDER RAW: %s', decoded)
                # Format: '(\HasNoChildren) "." "INBOX.Sent"'
                # Parse flags, delimiter, and name properly
                flags_end = decoded.index(')')
                flags = decoded[1:flags_end].split()
                rest = decoded[flags_end + 2:].strip()
                # Use regex to extract quoted delimiter and quoted name
                m = re.match(r'"(.*?)"\s+"(.+)"', rest)
                if m:
                    delim = m.group(1)
                    name = m.group(2)
                else:
                    # Fallback: delimiter might be unquoted (e.g. just a dot)
                    # Format: '. "folder"' or '"\\." "folder"'
                    m2 = re.match(r'(\S+)\s+"(.+)"', rest)
                    if m2:
                        delim = m2.group(1).strip('"')
                        name = m2.group(2)
                    else:
                        parts = rest.split(' ', 1)
                        delim = parts[0].strip('"') if parts[0] != 'NIL' else '/'
                        name = parts[1].strip('" ') if len(parts) > 1 else ''
                _logger.info('IMAP FOLDER PARSED: name=%r delim=%r (%d chars) hex=%s', name, delim, len(delim), delim.encode().hex())
                result.append({
                    'name': name,
                    'delimiter': delim,
                    'flags': flags,
                })
            return result
        finally:
            conn.logout()

    def action_fetch_mails(self, folder='INBOX', offset=0, limit=80):
        conn = self._connect_imap()
        try:
            conn.select(folder)
            _, data = conn.search(None, 'ALL')
            uids = data[0].split()
            total = len(uids)
            page = uids[max(0, total - offset - limit):total - offset] if uids else []
            messages = []
            for uid in page:
                _, msg_data = conn.fetch(uid, '(FLAGS BODY.PEEK[HEADER])')
                raw = msg_data[0]
                if not isinstance(raw, tuple):
                    continue
                flags, header_raw = raw
                msg = BytesParser(policy=policy.default).parsebytes(header_raw)
                messages.append({
                    'uid': int(uid),
                    'seen': b'\\Seen' in flags,
                    'flagged': b'\\Flagged' in flags,
                    'from_': str(msg.get('From', '')),
                    'subject': str(msg.get('Subject', '(No subject)')),
                    'date': str(msg.get('Date', '')),
                    'message_id': str(msg.get('Message-ID', '')),
                })
            return {
                'messages': list(reversed(messages)),
                'total': total,
            }
        finally:
            conn.logout()

    def action_fetch_mail(self, folder, uid):
        cache_key = f"{self.env.user.id}:{folder}:{uid}"
        if cache_key in _mail_cache:
            return _mail_cache[cache_key]

        conn = self._connect_imap()
        try:
            conn.select(folder)
            _, data = conn.fetch(str(uid).encode(), '(RFC822 FLAGS)')
            raw = data[0]
            if not isinstance(raw, tuple):
                return None
            flags, raw_email = raw
            msg = BytesParser(policy=policy.default).parsebytes(raw_email)
            body_html = ''
            body_text = ''
            attachments = []
            if msg.is_multipart():
                for part in msg.walk():
                    ct = part.get_content_type()
                    if ct == 'text/html' and not body_html:
                        body_html = part.get_content()
                    elif ct == 'text/plain' and not body_text:
                        body_text = part.get_content()
                    elif part.get_filename():
                        attachments.append({
                            'filename': part.get_filename(),
                            'content_type': ct,
                        })
            else:
                body_html = msg.get_content()
            result = {
                'uid': int(uid),
                'seen': b'\\Seen' in flags,
                'from_': str(msg.get('From', '')),
                'to_': str(msg.get('To', '')),
                'cc_': str(msg.get('Cc', '')),
                'subject': str(msg.get('Subject', '(No subject)')),
                'date': str(msg.get('Date', '')),
                'body_html': body_html or body_text or '(No content)',
                'attachments': attachments,
            }
            _mail_cache[cache_key] = result
            # Limit cache size
            if len(_mail_cache) > 500:
                keys = list(_mail_cache.keys())
                for k in keys[:100]:
                    del _mail_cache[k]
            return result
        finally:
            conn.logout()

    def action_clear_cache(self):
        """Clear the mail detail cache."""
        _mail_cache.clear()
        return {'ok': True}

    def action_send_mail(self, to, subject, body, cc=''):
        password = self._decrypt_password()
        if not password:
            raise UserError(_("No IMAP password configured."))
        host = self._get_config('smtp_server', 'localhost')
        port = int(self._get_config('smtp_port', 587))
        encryption = self._get_config('smtp_encryption', 'starttls')
        user = self.env.user.postfix_mail
        conn = smtplib.SMTP(host, port)
        if encryption == 'starttls':
            conn.starttls()
        elif encryption == 'ssl':
            conn = smtplib.SMTP_SSL(host, port)
        conn.login(user, password)
        try:
            msg = EmailMessage()
            msg['From'] = user
            msg['To'] = to
            if cc:
                msg['Cc'] = cc
            msg['Subject'] = subject
            msg.set_content(body)
            conn.send_message(msg)
        finally:
            conn.quit()

    def action_set_flag(self, folder, uids, flag, value):
        conn = self._connect_imap()
        try:
            conn.select(folder)
            uid_list = ','.join(str(u) for u in uids)
            cmd = '+FLAGS' if value else '-FLAGS'
            imap_flag = '\\Seen' if flag == 'seen' else '\\Flagged'
            conn.uid('STORE', uid_list, cmd, imap_flag)
        finally:
            conn.logout()

    # ══════════════════════════════════════════════════════════════
    # Polling (cron, per-user) — mail-infrastruktur, ingen AI
    # ══════════════════════════════════════════════════════════════

    @api.model
    def action_poll_all(self):
        """Poll every user with imap_poll_enabled=True.

        Körs av cron via user.mail.poll. Varje användare pollas med sin
        egen IMAP-identitet (with_user) och sina egna credentials. Ett fel
        för en användare blockerar aldrig övriga.
        """
        users = self.env['res.users'].search(
            [('imap_poll_enabled', '=', True)])
        total = 0
        for user in users:
            try:
                total += self.with_user(user.id).action_poll_user()
            except Exception as e:
                _logger.error(
                    'Mail poll failed for user %s (%s): %s',
                    user.login, user.id, e)
        return total

    def action_poll_user(self):
        """Poll ONE user's INBOX (env.user = target user).

        Readonly-säker: använder BODY.PEEK så att \\Seen aldrig sätts och
        inga flaggor/mappar ändras. Hämtar mail sedan last_imap_sync,
        normaliserar, deduplicerar via Message-ID, anropar
        _on_new_messages() per batch och uppdaterar last_imap_sync.
        """
        user = self.env.user
        since = user.last_imap_sync
        conn = self._connect_imap()
        try:
            conn.select('INBOX')
            if since:
                date_str = since.strftime('%d-%b-%Y')
                typ, data = conn.search(None, '(SINCE %s)' % date_str)
            else:
                typ, data = conn.search(None, 'ALL')
            if typ != 'OK':
                return 0
            uids = data[0].split() if data and data[0] else []
            processed = set(self._get_processed_message_ids(user.id))
            messages = []
            for uid in uids:
                typ2, msg_data = conn.fetch(uid, '(BODY.PEEK[])')
                if (typ2 != 'OK' or not msg_data
                        or not isinstance(msg_data[0], tuple)):
                    continue
                _flags_raw, raw = msg_data[0]
                try:
                    norm = self._normalize_message(
                        raw, folder='INBOX', uid=int(uid))
                except Exception as e:
                    _logger.warning(
                        'Normalize failed for %s uid=%s: %s',
                        user.login, uid, e)
                    continue
                if not norm:
                    continue
                key = norm.get('dedup_key')
                if key and key in processed:
                    continue
                if key:
                    processed.add(key)
                messages.append(norm)
            if messages:
                self._on_new_messages(messages)
                self._record_processed(user.id, messages)
            user.write({'last_imap_sync': fields.Datetime.now()})
            return len(messages)
        finally:
            conn.logout()

    def _get_processed_message_ids(self, user_id):
        """Alla redan processade dedup-nycklar för användaren."""
        return self.env['user.mail.processed'].sudo().search(
            [('user_id', '=', user_id)]).mapped('message_id')

    def _record_processed(self, user_id, messages):
        """Beständig dedup: spara Message-ID/dedup-nyckel per användare."""
        vals = []
        for m in messages:
            key = m.get('dedup_key')
            if not key:
                continue
            vals.append({'user_id': user_id, 'message_id': key})
        if vals:
            self.env['user.mail.processed'].sudo().create(vals)

    @staticmethod
    def _dedup_key(norm):
        """Message-ID när det finns; annars hash av (from, subject, date)."""
        mid = (norm.get('message_id') or '').strip()
        if mid:
            return mid
        raw = '%s|%s|%s' % (
            norm.get('from_', ''),
            norm.get('subject', ''),
            norm.get('date', ''),
        )
        return 'h:' + hashlib.md5(
            raw.encode('utf-8', 'ignore')).hexdigest()

    def _normalize_message(self, raw_bytes, folder='INBOX', uid=None):
        """Parsa en rå RFC822-message till den normaliserade dict-formen.

        Returns dict: message_id, subject, from_, to_, date, body_text,
        body_html, attachments (list of {filename, content_type}), folder,
        raw (bytes) och dedup_key.
        """
        msg = BytesParser(policy=policy.default).parsebytes(raw_bytes)
        body_text = ''
        body_html = ''
        attachments = []
        if msg.is_multipart():
            for part in msg.walk():
                ct = part.get_content_type()
                fn = part.get_filename()
                if fn:
                    attachments.append({'filename': fn, 'content_type': ct})
                elif ct == 'text/plain' and not body_text:
                    body_text = part.get_content() or ''
                elif ct == 'text/html' and not body_html:
                    body_html = part.get_content() or ''
        else:
            ct = msg.get_content_type()
            if ct == 'text/html':
                body_html = msg.get_content() or ''
            else:
                body_text = msg.get_content() or ''
        norm = {
            'uid': uid,
            'message_id': str(msg.get('Message-ID', '')).strip(),
            'subject': str(msg.get('Subject', '')),
            'from_': str(msg.get('From', '')),
            'to_': str(msg.get('To', '')),
            'date': str(msg.get('Date', '')),
            'body_text': body_text,
            'body_html': body_html,
            'attachments': attachments,
            'folder': folder,
            'raw': raw_bytes,
        }
        norm['dedup_key'] = self._dedup_key(norm)
        return norm


    # ══════════════════════════════════════════════════════════════
    # Drafts & mappar (Skiva 2 — utåtgående, HITL-gat)
    # ══════════════════════════════════════════════════════════════

    def _drafts_folder(self):
        """Drafts-mapp: LIST-detektering (Drafts/Utkast) med konfig-fallback."""
        param = self.env['ir.config_parameter'].sudo().get_param(
            'user_mail_imap.drafts_folder', '')
        if param:
            return param
        try:
            conn = self._connect_imap()
            try:
                for item in conn.list()[1]:
                    decoded = item.decode('utf-8', 'ignore')
                    m = re.search(r'\s"\S+"\s"([^"]+)"', decoded)
                    name = m.group(1) if m else decoded.rsplit(' ', 1)[-1]
                    if name.lower() in ('drafts', 'utkast'):
                        return name
            finally:
                conn.logout()
        except Exception as e:
            _logger.warning('Drafts folder detection failed: %s', e)
        return 'Drafts'

    def action_append_draft(self, to, subject, body, in_reply_to=None,
                            cc='', folder=None):
        """APPEND:a ett utkast till användarens Drafts-mapp (IMAP).

        Körs i användarens kontext (env.user). Returnerar UID (via
        UIDPLUS/appenduid eller sök på Message-ID).
        """
        user = self.env.user
        conn = self._connect_imap()
        try:
            folder = folder or self._drafts_folder()
            msg = EmailMessage()
            msg['From'] = user.postfix_mail or ''
            msg['To'] = to
            if cc:
                msg['Cc'] = cc
            msg['Subject'] = subject
            if in_reply_to:
                msg['In-Reply-To'] = in_reply_to
                msg['References'] = in_reply_to
            msg['Date'] = email.utils.formatdate(localtime=True)
            mid = '<%s@vertel>' % hashlib.md5(
                ('%s%s%s' % (to, subject, fields.Datetime.now())).encode()
            ).hexdigest()
            msg['Message-ID'] = mid
            msg.set_content(body)
            typ, data = conn.append(
                folder, '\\Draft', imaplib.Time2Internaldate(time.time()),
                msg.as_bytes())
            # UIDPLUS: appenduid returnerar UID
            uid = None
            if data and data[0]:
                m = re.search(rb'APPENDUID \d+ (\d+)', data[0])
                if m:
                    uid = int(m.group(1))
            if uid is None:
                # Fallback: sök på Message-ID
                try:
                    conn.select(folder)
                    typ2, sdata = conn.search(None, '(HEADER Message-ID "%s")' % mid)
                    if typ2 == 'OK' and sdata and sdata[0]:
                        uid = int(sdata[0].split()[-1])
                except Exception:
                    pass
            return {'folder': folder, 'uid': uid, 'message_id': mid}
        finally:
            conn.logout()

    def action_move(self, folder, uids, to_folder):
        """Flytta mail mellan mappar (UID MOVE, fallback COPY+DEL+EXPUNGE)."""
        conn = self._connect_imap()
        try:
            conn.select(folder)
            uid_list = ','.join(str(u) for u in uids)
            typ, _data = conn.uid('MOVE', uid_list, to_folder)
            if typ != 'OK':
                # Fallback: COPY + \Deleted + EXPUNGE
                conn.uid('COPY', uid_list, to_folder)
                conn.uid('STORE', uid_list, '+FLAGS', '\\Deleted')
                conn.expunge()
            return True
        finally:
            conn.logout()

    def action_ensure_folder(self, folder):
        """Skapa mapp om den inte finns (best-effort)."""
        conn = self._connect_imap()
        try:
            try:
                conn.create(folder)
                return True
            except Exception:
                return False
        finally:
            conn.logout()

    def action_fetch_draft_raw(self, folder, uid):
        """Hämta råa innehållet i ett utkast (för HITL-skick)."""
        conn = self._connect_imap()
        try:
            conn.select(folder)
            typ, data = conn.fetch(str(uid).encode(), '(BODY.PEEK[])')
            if typ == 'OK' and data and isinstance(data[0], tuple):
                return data[0][1]
            return None
        finally:
            conn.logout()

    def _on_new_messages(self, messages):
        """Hook — anropas per användarbatch efter en lyckad pollning.

        Bryggmoduler (t.ex. user_mail_ai) ärver user.mail.imap och
        överlagrar denna för att konsumera de normaliserade mailet.
        Här: no-op.
        """
        return len(messages)
