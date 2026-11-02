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
            f'Godkänn {action_type} för {item.coworker.name}',
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
