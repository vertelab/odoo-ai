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

Användning (social_ai när den byggs, mail-kanalen vid delat behov):
    channel_registry.register('mail', adapter=mail_adapter,
                              processor=mail_processor)
    await process_item(item)
"""

from dataclasses import dataclass, field
from typing import Any, Optional, Protocol


# ── Normaliserat item ────────────────────────────────────────────────

@dataclass
class NormalizedItem:
    """Kanal-neutralt inkommande item.

    Attachments: lista av dicts {filename, content_type, ...}.
    """
    channel: str
    external_id: str
    sender: str = ''
    content: str = ''
    received_at: str = ''
    attachments: list = field(default_factory=list)


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

class ChannelRegistry:
    """Registrering av kanal-adaptrar + processorer."""

    def __init__(self):
        self._adapters: dict = {}
        self._processors: dict = {}

    def register(self, name: str, adapter: Optional[ChannelAdapter] = None,
                 processor: Optional[ItemProcessor] = None) -> str:
        """Registrera en kanal. Adapter och/eller processor kan anges."""
        if adapter is not None:
            self._adapters[name] = adapter
        if processor is not None:
            self._processors[name] = processor
        return name

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
    required = ('fetch_new', 'normalize', 'dispose',
                'draft_outbound', 'send_outbound')
    return all(callable(getattr(obj, name, None)) for name in required)


def satisfies_processor(obj) -> bool:
    """Strukturell kontroll: uppfyller objektet ItemProcessor-kontraktet?"""
    required = ('classify', 'dispose', 'hitl', 'nudge', 'remember')
    return all(callable(getattr(obj, name, None)) for name in required)
