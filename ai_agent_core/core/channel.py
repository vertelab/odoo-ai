# -*- coding: utf-8 -*-
"""Channel-adapter-kontraktet (Skiva 4-förberedelse, extraherat ur mail).

Bevisat av mail-hjälpredan (user_mail_ai). Kontraktet är minimalt och
domän-fritt:

- NormalizedItem — kanal-neutralt item.
- ChannelAdapter — kanalens in/ut-adaptrar.
- ItemProcessor — domänbunden pipeline-implementation (klassificering →
  disposition → HITL → nudge → minne).
- channel_registry + process_item — gemensam loop som kanaler anropar
  istället för att duplicera loopen.

Sedan channel-adapter-wiring (2026-11-02) har kontraktet
produktionsanropare: `models/ai_coworker.py` registrerar `mail`-kanalen
vid moduluppstart och `ai.coworker.mail()` / `ai.coworker.chat()` kör sina
inkommande items genom `process_item()`.

Användning:
    channel_registry.register('mail', adapter=mail_adapter,
                              processor=mail_processor)
    await process_item(item)
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol

_logger = logging.getLogger(__name__)


# ── Normaliserat item ────────────────────────────────────────────────

@dataclass
class NormalizedItem:
    """Kanal-neutralt inkommande item.

    Attachments: lista av dicts {filename, content_type, ...}.

    Kanal-specifika fält (tillagda i channel-adapter-wiring §3–§5):

    - `raw` — det rå-objekt itemet normaliserades från (`mail.message`,
      `mail.message` i en discuss-kanal). Adaptern behöver det för att
      kunna skicka svaret tillbaka på samma tråd.
    - `subject` — kanalens ämne (mail: `message.subject`; tomt i chat).
    - `session` — `ai.coworker.session` som körningen tillhör. Sätts av
      kanalen EFTER att sessionen skapats; HITL kräver den.
    - `coworker` — `ai.coworker` som äger körningen. Adaptern behöver den
      för avsändare/bolag när svaret ska ut.
    - `reply_text` — svaret som ska ut. Sätts av kanalen efter `run()`;
      processorns `dispose()` läser den.
    """
    channel: str
    external_id: str
    sender: str = ''
    content: str = ''
    received_at: str = ''
    attachments: list = field(default_factory=list)
    # Kanal-specifik kontext (se docstring) — aldrig serialiserad.
    raw: Any = None
    subject: str = ''
    session: Any = None
    coworker: Any = None
    reply_text: str = ''

    @property
    def env(self):
        """Odoo-env ur `coworker` (None när itemet inte är bundet)."""
        return self.coworker.env if self.coworker is not None else None


# ── Kanal-adaptrar ──────────────────────────────────────────────────

class ChannelAdapter(Protocol):
    """In-/utåtgående adapter för en kanal (IMAP, social media, …)."""

    def fetch_new(self, user, since: Optional[str] = None) -> list:
        """Hämta nya items sedan `since`. Returnerar lista av NormalizedItem."""
        ...

    def normalize(self, raw: Any) -> NormalizedItem:
        """Normalisera ett rå-objekt till NormalizedItem."""
        ...

    def dispose(self, item: NormalizedItem, disposition: str) -> None:
        """Verkställ en disposition (create/link/draft/nudge/handoff)."""
        ...

    def draft_outbound(self, user, item: NormalizedItem,
                       content: str) -> dict:
        """Förbered ett utkast (IMAP-Drafts, social-post-skiss)."""
        ...

    def send_outbound(self, user, item: NormalizedItem,
                      content: str) -> None:
        """Skicka/publicera (anropas ENDAST efter HITL-godkännande)."""
        ...


class ItemProcessor(Protocol):
    """Domänbunden pipeline-implementation (klassificering→…→minne).

    Implementeras av kanalbryggan (user_mail_ai idag, social_ai senare)
    med ai_agent_core + OKF + graf + ai.coworker.hitl som substrat.
    """

    async def classify(self, item: NormalizedItem) -> dict:
        """Zero-shot-klassificering. Retur:
        {disposition, action_type?, hitl_required?, hitl_context?,
         nudge_message?, …}"""
        ...

    def dispose(self, item: NormalizedItem, disposition: str) -> None:
        ...

    def hitl(self, item: NormalizedItem, action_type: str,
             context: dict) -> Any:
        """Begär godkännande via ai.coworker.hitl. Retur har .approved."""
        ...

    def nudge(self, item: NormalizedItem, message: str) -> None:
        ...

    def remember(self, item: NormalizedItem) -> None:
        """Spara till minne (OKF + graf)."""


# ── Generiska dispositionstyper ─────────────────────────────────────

DISPOSITIONS = ('create', 'link', 'draft', 'nudge', 'handoff')

# Dispositioner som kräver HITL före verkställan
_HITL_GATED = ('create', 'link', 'handoff')


# ── Registry + gemensam loop ────────────────────────────────────────

#: Metoderna som respektive kontrakt kräver. `satisfies_*` och
#: registreringsgrinden läser SAMMA lista — annars kan de två glida isär och
#: grinden godkänna något som `satisfies_*` underkänner.
_ADAPTER_METHODS = ('fetch_new', 'normalize', 'dispose',
                    'draft_outbound', 'send_outbound')
_PROCESSOR_METHODS = ('classify', 'dispose', 'hitl', 'nudge', 'remember')


class ChannelRegistry:
    """Registrering av kanal-adaptrar + processorer."""

    def __init__(self):
        self._adapters: dict = {}
        self._processors: dict = {}
        # Kanaler som redan fått 'no_processor'-varningen (D3). En varning
        # räcker — annars skriver varje item om samma glömda registrering.
        self._warned_no_processor: set = set()

    def register(self, name: str, adapter: Optional[ChannelAdapter] = None,
                 processor: Optional[ItemProcessor] = None) -> str:
        """Registrera en kanal. Adapter och/eller processor kan anges.

        Registreringen sker ALLTID (en kanal som inte uppfyller kontraktet
        ska inte krascha) — men ett ofullständigt objekt varnas om, med
        namnet på varje saknad metod (D5).
        """
        if adapter is not None:
            self._check_contract(name, 'adapter', adapter,
                                 satisfies_adapter, _ADAPTER_METHODS)
            self._adapters[name] = adapter
        if processor is not None:
            self._check_contract(name, 'processor', processor,
                                 satisfies_processor, _PROCESSOR_METHODS)
            self._processors[name] = processor
        return name

    @staticmethod
    def _check_contract(name, kind, obj, check, required):
        """Varna när ett registrerat objekt inte uppfyller kontraktet."""
        if check(obj):
            return
        missing = [
            method for method in required
            if not callable(getattr(obj, method, None))
        ]
        _logger.warning(
            'channel_registry: kanalen %r registreras med en %s som inte '
            'uppfyller kontraktet — saknar: %s',
            name, kind, ', '.join(missing) or '(okänt)')

    def forget_no_processor_warning(self, name: str) -> None:
        """Glöm att kanalen varnats (används av tester och omregistrering)."""
        self._warned_no_processor.discard(name)

    def get_adapter(self, name: str) -> Optional[ChannelAdapter]:
        return self._adapters.get(name)

    def get_processor(self, name: str) -> Optional[ItemProcessor]:
        return self._processors.get(name)

    def channels(self) -> list:
        return sorted(self._adapters.keys())


channel_registry = ChannelRegistry()


async def process_item(item: NormalizedItem, processor: Optional[ItemProcessor] = None,
                       registry: Optional[ChannelRegistry] = None) -> str:
    """Gemensam loop: klassificering → disposition → HITL → nudge → minne.

    Kanaler anropar denna istället för att implementera loopen själva.

    Returns disposition-strängen ('create', 'link', 'draft', 'nudge',
    'handoff') eller 'waiting_hitl' / 'no_processor'.
    """
    reg = registry or channel_registry
    proc = processor or reg.get_processor(item.channel)
    if proc is None:
        # D3: degraderingen är rätt (en kanal utan processor ska inte
        # krascha) men får inte vara tyst — en glömd registrering ska synas
        # i loggen i stället för att bara göra ingenting. En varning per
        # kanal räcker.
        if item.channel not in reg._warned_no_processor:
            reg._warned_no_processor.add(item.channel)
            _logger.warning(
                'process_item: ingen processor registrerad för kanalen %r — '
                "returnerar 'no_processor'. Registrera kanalen med "
                'channel_registry.register(%r, processor=...).',
                item.channel, item.channel)
        return 'no_processor'

    classification = await proc.classify(item) or {}
    disposition = classification.get('disposition') or 'nudge'
    if disposition not in DISPOSITIONS:
        disposition = 'nudge'

    # HITL-gate: utåtgående/kopplande dispositioner kräver godkännande
    if disposition in _HITL_GATED and classification.get('hitl_required'):
        approval = proc.hitl(
            item,
            classification.get('action_type') or disposition,
            classification.get('hitl_context') or {},
        )
        if not getattr(approval, 'approved', False):
            proc.remember(item)
            return 'waiting_hitl'

    proc.dispose(item, disposition)
    nudge_message = classification.get('nudge_message')
    if nudge_message:
        proc.nudge(item, nudge_message)
    proc.remember(item)
    return disposition


def satisfies_adapter(obj) -> bool:
    """Strukturell kontroll: uppfyller objektet ChannelAdapter-kontraktet?"""
    return all(callable(getattr(obj, name, None)) for name in _ADAPTER_METHODS)


def satisfies_processor(obj) -> bool:
    """Strukturell kontroll: uppfyller objektet ItemProcessor-kontraktet?"""
    return all(callable(getattr(obj, name, None))
               for name in _PROCESSOR_METHODS)
