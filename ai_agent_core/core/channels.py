# -*- coding: utf-8 -*-
"""Kanal-adaptrar och -processorer för `ai.coworker` (channel-adapter-wiring).

Kontraktet bor i `core/channel.py` och är domän-fritt. Här bor
implementationerna — de får röra Odoo-modeller, för det är deras uppgift.

Uppdelningen (design D2):

- `MailChannelAdapter` — kanalspecifik: läsa `mail.message`, normalisera,
  skicka `mail.mail`. Vet inget om vad en disposition *betyder*.
- `MailItemProcessor` — domänbunden: klassificera, verkställa, HITL,
  nudge, minne. Vet inget om hur ett mail läses.

`ai.coworker.mail()` använder båda via `process_item()` — den äger inte
längre sin egen loop.

VARFÖR: specen (`surfaces`) säger att kanalerna ska anropa den gemensamma
loopen. Före den här ändringen anropade ingen den, och mail/chat hade var
sin — med divergens som följd (mail saknade cache-snabbspåret, chat saknade
bilageextraktionen).
"""

import asyncio
import base64
import logging
import threading

from odoo.tools.mail import html2plaintext

from odoo.addons.ai_agent_core.core.channel import (
    NormalizedItem,
    channel_registry,
    process_item,
)

_logger = logging.getLogger(__name__)


# ── Delade hjälpare ─────────────────────────────────────────────────

def run_coroutine_sync(coro):
    """Kör en coroutine från synkron Odoo-kod — utan att krocka med en loop.

    `process_item()` är `async` (kontraktet är skrivet så), men `mail()` och
    `chat()` är synkrona. Två fall:

    1. Ingen loop körs i tråden → `asyncio.run()`.
    2. En loop körs redan (t.ex. `run()` anropad inifrån `/ai/stream`) →
       coroutinen körs i en egen tråd med egen loop. Att starta en ny loop i
       samma tråd ger "Cannot run the event loop while another loop is
       running"; tråden är samma mönster som `run()`:s nested-loop-guard.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    result = {}

    def _target():
        try:
            result['value'] = asyncio.run(coro)
        except BaseException as exc:  # noqa: BLE001 — återkastas nedan
            result['error'] = exc

    thread = threading.Thread(target=_target, daemon=True)
    thread.start()
    thread.join()
    if 'error' in result:
        raise result['error']
    return result.get('value')


def attachment_texts(attachment_ids):
    """Extrahera läsbar text ur bilagor — formatet mail-vägen alltid haft.

    Returnerar `['--- <namn> ---\\n<text>', ...]` (max 2000 tecken per
    bilaga; bilagor över 10 000 tecken hoppas över). En bilaga som inte kan
    läsas hoppas över — extraktionen får aldrig fälla mailet.
    """
    from odoo.addons.ai_agent_core.models.ai_coworker import _extract_text

    texts = []
    for att in attachment_ids:
        try:
            text = _extract_text(att.name, base64.b64decode(att.datas))
            if text and len(text) < 10000:
                texts.append(f'--- {att.name} ---\n{text[:2000]}')
        except Exception:
            # best-effort: en oläsbar bilaga får inte fälla mailet
            continue
    return texts


def attachments_prompt_section(texts):
    """Prompt-avsnittet för bilagor — tomt när det inte finns några."""
    if not texts:
        return ''
    return '\n\n## Attachments\n' + '\n'.join(texts)


def last_assistant_content(session, limit=4000):
    """Senaste assistant-radens innehåll (sista raden kan vara en tool-rad)."""
    if not session:
        return ''
    last_line = session.session_line_ids.filtered(
        lambda line: line.role == 'assistant'
    ).sorted('sequence', reverse=True)[:1]
    if not last_line or not last_line.content:
        return ''
    return last_line.content[:limit]


# ── Mail ────────────────────────────────────────────────────────────

class MailChannelAdapter:
    """`ChannelAdapter` för inkommande/utgående mail.

    Adaptern är kanalspecifik och tillståndsfri: det item som ska besvaras
    bär sitt eget `mail.message` i `raw`. `fetch_new()` används inte av
    `mail()` (meddelandet kommer från alias-routingen) men ingår i
    kontraktet — och är den väg en framtida IMAP-poll skulle ta.
    """

    channel = 'mail'

    def fetch_new(self, env, since=None):
        """Nya inkommande mail sedan `since` (ISO-datum), normaliserade."""
        domain = [('message_type', '=', 'email')]
        if since:
            domain.append(('date', '>', since))
        messages = env['mail.message'].search(domain, order='date asc')
        return [self.normalize(message) for message in messages]

    def normalize(self, raw):
        """`mail.message` → `NormalizedItem`."""
        try:
            body_text = html2plaintext(raw.body or '')
        except Exception:
            # fallback: rå HTML är bättre än ingen text alls
            body_text = raw.body or ''
        attachments = [
            {
                'filename': att.name or '',
                'content_type': att.mimetype or '',
                'attachment_id': att.id,
            }
            for att in raw.attachment_ids
        ]
        return NormalizedItem(
            channel=self.channel,
            external_id=raw.message_id or str(raw.id),
            sender=raw.email_from or '',
            content=body_text,
            received_at=str(raw.date or ''),
            attachments=attachments,
            raw=raw,
        )

    def dispose(self, item, disposition):
        """Mail-kanalen verkställer inget själv vid dispositionen.

        Dispositionen betyder "vad agenten ska göra" och verkställs av
        `MailItemProcessor.dispose()`. Adapterns dispose finns för kontraktet
        (och för en framtida kanalspecifik sidoeffekt, t.ex. att flytta
        meddelandet till en mapp).
        """
        return None

    def draft_outbound(self, env, item, content):
        """Förbered ett svarsutkast (skickas inte)."""
        return {
            'subject': f'Re: {item.subject or "AI Response"}',
            'email_to': item.sender,
            'body': content,
        }

    def send_outbound(self, env, item, content):
        """Skicka svaret som mail — ENDAST efter godkännande.

        Anropas av `MailItemProcessor.dispose()`. Utskicket går via
        `_send_mail_reply()`, så ämne, `reply_to` och avsändare blir exakt
        som före migreringen.
        """
        from odoo.addons.ai_agent_core.models.ai_coworker import _send_mail_reply

        if not item.raw:
            return None
        return _send_mail_reply(item.raw, content, item.coworker)


class MailItemProcessor:
    """`ItemProcessor` för mail-kanalen.

    Klassificeringen är REGELBASERAD (design D5): bilagor → `create`,
    annars → `nudge`. Ingen LLM-klassificering — ett LLM-anrop per mail är
    en kostnad som ska motiveras separat, och den här ändringen är en
    refaktor utan beteendeändring.
    """

    channel = 'mail'

    async def classify(self, item):
        """Regelbaserad disposition — inga LLM-anrop."""
        if item.attachments:
            return {'disposition': 'create', 'hitl_required': False}
        return {'disposition': 'nudge'}

    def dispose(self, item, disposition):
        """Verkställ dispositionen.

        Mail-kanalens enda utåtgående handling är svarsmailet. Både `nudge`
        (vanligt mail) och `create` (mail med bilaga) besvaras — precis som
        före migreringen. `item.reply_text` sätts av `mail()` efter `run()`.
        """
        if disposition not in ('nudge', 'create'):
            return None
        if not item.reply_text:
            return None
        adapter = channel_registry.get_adapter(self.channel)
        if adapter is None:
            return None
        return adapter.send_outbound(item.env, item, item.reply_text)

    def hitl(self, item, action_type, context):
        """Begär godkännande via `ai.coworker.hitl`."""
        if not item.session:
            return type('Approval', (), {'approved': False})()
        request = item.coworker.with_context(
            _ai_context_model='ai.coworker.session',
            _ai_context_id=item.session.id,
        )._request_hitl(
            action_type,
            f'Approve {action_type} for {item.coworker.name}',
            context=context or {},
            session=item.session,
        )
        return type('Approval', (), {
            'approved': bool(request) and request.state == 'approved',
            'request': request,
        })()

    def nudge(self, item, message):
        """Mail-kanalen har ingen separat nudge-kanal — tyst no-op."""
        return None

    def remember(self, item):
        """Spara till minne — `run()` sköter sessionsminnet.

        Ett extra minnesskriv här skulle dubbla det som redan sker i `run()`
        och i `_maybe_learn_async()`. Metoden finns för kontraktet.
        """
        return None


def register_mail_channel():
    """Registrera mail-kanalen i `channel_registry` (idempotent).

    Anropas av `post_init_hook` så att kanalen finns efter en
    moduluppdatering, och av testerna.
    """
    channel_registry.register(
        'mail',
        adapter=MailChannelAdapter(),
        processor=MailItemProcessor(),
    )
    return 'mail'


def process_mail_item(item):
    """Kör ett mail-item genom den gemensamma loopen (synkront)."""
    return run_coroutine_sync(process_item(item))


# ── Chat / channel (Discuss) ────────────────────────────────────────

class DiscussChannelAdapter:
    """`ChannelAdapter` för Discuss (DM + kanal).

    Kanalen är *interaktiv* — det finns ingen kö att polla, så `fetch_new()`
    returnerar tomt. Det som behövs är normaliseringen (HTML → text,
    @mention-rensning) och postningen, och de bor här i stället för i
    `chat()`.
    """

    channel = 'chat'

    def fetch_new(self, env, since=None):
        """Ingen kö: Discuss-meddelanden kommer via `_route_message()`."""
        return []

    def normalize(self, raw, bot_user=None):
        """`mail.message` → `NormalizedItem`, med @mention rensad.

        `@<alias>`/`@<namn>` tas bort ur frågan — annars tolkar agenten
        "@allmän assistent" som en del av frågan. Även rester av
        @mention-HTML/Odoo-länkar (`[1] /odoo/res.partner/N`) rensas.
        """
        import re

        from odoo.tools.mail import html2plaintext

        try:
            msg_text = html2plaintext(raw.body or '')
        except Exception:
            # fallback: rå HTML är bättre än ingen text alls
            msg_text = raw.body or ''
        for token in (bot_user.channel_alias if bot_user else '',
                      bot_user.name if bot_user else ''):
            if token:
                msg_text = re.sub(
                    r'@' + re.escape(token), '', msg_text, flags=re.I)
        msg_text = re.sub(r'\[\d\]\s*/odoo/[^\s]+', '', msg_text)
        return NormalizedItem(
            channel=self.channel,
            external_id=str(raw.id or ''),
            sender=raw.email_from or '',
            content=msg_text.strip(),
            received_at=str(raw.date or ''),
            raw=raw,
        )

    def dispose(self, item, disposition):
        """Discuss-kanalen verkställer inget själv — se processorn."""
        return None

    def draft_outbound(self, env, item, content):
        """Discusssvar skickas direkt; inget utkaststeg finns."""
        return {'body': content}

    def send_outbound(self, env, item, content):
        """Posta svaret i kanalen som botens partner.

        Markdown → HTML (`extra` ger tabellstöd) och `Markup` så Odoo
        renderar som HTML i stället för att escapera. Faller tillbaka på
        råtext om `markdown`/`markupsafe` saknas — svaret ska ut ändå.
        """
        if not item.channel_record:
            return None
        body_html = content
        try:
            import markdown
            body_html = markdown.markdown(body_html, extensions=['extra'])
        except Exception:
            # fallback: råtext postas i stället för att svaret uteblir
            body_html = content
        try:
            from markupsafe import Markup
            body_html = Markup(body_html)
        except Exception:
            # fallback: en vanlig sträng escapas av Odoo — läsbart men fult
            body_html = content
        author_id = (
            item.bot_user.partner_id.id
            if item.bot_user and item.bot_user.partner_id
            else item.coworker.partner_id.id)
        return item.channel_record.message_post(
            body=body_html, author_id=author_id,
            message_type='comment', subtype_xmlid='mail.mt_comment')


class ChatItemProcessor:
    """`ItemProcessor` för Discuss.

    De tre särdrag `chat()` hade egen kod för kan uttryckas i kontraktet
    (task 5.1):

    - **@mention-rensning** → `DiscussChannelAdapter.normalize()` (kanalens
      sak, inte domänens).
    - **historik-kontext** → `context()` (den här klassen), läst av `chat()`.
    - **cache-snabbspår** → `dispose()` (den här klassen): en `cache_hit`
      i itemet betyder "posta det sparade svaret och spara sessionen".

    Klassificeringen är regelbaserad — ingen LLM.
    """

    channel = 'chat'

    async def classify(self, item):
        """Regelbaserad disposition — inga LLM-anrop."""
        if item.cache_hit:
            return {'disposition': 'draft'}
        return {'disposition': 'nudge'}

    def context(self, item):
        """Historik-kontexten som `chat()` lägger i systemprompten.

        Läser de senaste meddelandena i kanalen (samma fråga och samma
        gräns som före migreringen) och formaterar dem som
        `[role] text`-rader. Tom sträng när historiken är avstängd eller
        kanalen saknas.
        """
        if not item.use_history or not item.channel_record:
            return ''
        messages = item.env['mail.message'].search([
            ('model', '=', 'discuss.channel'),
            ('res_id', '=', item.channel_record.id),
        ], limit=item.history_limit, order='create_date asc')
        lines = []
        for msg in messages:
            role = 'assistant' if msg.author_id == item.bot_user else 'user'
            text = (msg.body or '')[:500]
            if text.strip():
                lines.append(f'[{role}] {text}')
        if not lines:
            return ''
        return '\n'.join(lines[-item.history_limit:])

    def dispose(self, item, disposition):
        """Verkställ dispositionen.

        - `draft` (cache-träff): spara sessionen med fråga + sparat svar och
          posta svaret — ingen körning.
        - `nudge` (vanlig tur): posta `item.reply_text`, som `chat()` sätter
          efter `run()`.
        """
        if disposition == 'draft':
            self._record_cache_hit(item)
        elif disposition != 'nudge':
            return None
        if not item.reply_text:
            return None
        adapter = channel_registry.get_adapter(self.channel)
        if adapter is None:
            return None
        return adapter.send_outbound(item.env, item, item.reply_text)

    def hitl(self, item, action_type, context):
        """Begär godkännande via `ai.coworker.hitl`."""
        if not item.session:
            return type('Approval', (), {'approved': False})()
        request = item.coworker.with_context(
            _ai_context_model='ai.coworker.session',
            _ai_context_id=item.session.id,
        )._request_hitl(
            action_type,
            f'Approve {action_type} for {item.coworker.name}',
            context=context or {},
            session=item.session,
        )
        return type('Approval', (), {
            'approved': bool(request) and request.state == 'approved',
            'request': request,
        })()

    def nudge(self, item, message):
        """Ingen separat nudge-kanal i Discuss — tyst no-op."""
        return None

    def remember(self, item):
        """Spara till minne — `run()` och `_maybe_learn_async()` äger det."""
        return None

    def _record_cache_hit(self, item):
        """Spåra en cache-träff som en egen session + två rader.

        Sessionen bär record-identiteten (`ai_record_*`) så att nästa
        cachesökning kan matcha på SAMMA record — utan den tappas
        kopplingen och en liknande fråga kan återigen besvaras med en annan
        records svar.
        """
        env = item.env
        vals = {
            'coworker_id': item.coworker.id, 'status': 'active',
            'init_type': 'chat',
            'name': f'Chat: {item.content[:50]}',
            'user_id': item.bot_user.id if item.bot_user else 1,
        }
        record_key = item.record_key
        if record_key:
            rk_model, rk_id = record_key[0], record_key[1]
            vals['ai_record_model'] = rk_model
            if isinstance(rk_id, int):
                vals['ai_record_id'] = rk_id
            else:
                vals['ai_record_ids'] = list(rk_id)
        session = env['ai.coworker.session'].create(vals)
        env['ai.coworker.session.line'].create({
            'session_id': session.id, 'sequence': 1,
            'role': 'user', 'content': item.content[:4000],
        })
        env['ai.coworker.session.line'].create({
            'session_id': session.id, 'sequence': 2,
            'role': 'assistant', 'content': item.reply_text[:4000],
        })
        session.status = 'done'


def register_chat_channel():
    """Registrera chat-kanalen i `channel_registry` (idempotent)."""
    channel_registry.register(
        'chat',
        adapter=DiscussChannelAdapter(),
        processor=ChatItemProcessor(),
    )
    return 'chat'


def process_chat_item(item):
    """Kör ett chat-item genom den gemensamma loopen (synkront)."""
    return run_coroutine_sync(process_item(item))


# ── Process-start ───────────────────────────────────────────────────
# Registreringen får INTE bara ligga i `post_init_hook`.
#
# Fällan (mätt 2026-11-09): `channel_registry` är en process-global
# singleton som töms när processen startar. `post_init_hook` körs EN gång
# vid (upp)installation — inte vid omstart. Efter varje omstart var
# registret därför tomt, `process_item()` returnerade 'no_processor' och
# `dispose()` anropades aldrig. Följden i drift: **mail-svar skickades
# aldrig**, och chat-svar postades aldrig via kanalvägen — utan ett enda
# fel i loggen, eftersom 'no_processor' var en tyst retur.
#
# Modulnivå-koden nedan körs när modulen importeras, alltså en gång per
# process — precis vad ett process-globalt register behöver. Den är
# idempotent (register() skriver över samma nyckel), så en hook som kör
# igen är ofarlig.
def _register_channels_on_import():
    """Registrera alla kanaler när modulen importeras (en gång per process)."""
    for register in (register_mail_channel, register_chat_channel):
        try:
            register()
        except Exception:
            # best-effort: en trasig kanal får inte hindra modulimporten.
            # Felet syns i process_item() som 'no_processor' med en varning.
            _logger.warning(
                '%s failed at import — the channel is unregistered',
                register.__name__, exc_info=True)


_register_channels_on_import()
