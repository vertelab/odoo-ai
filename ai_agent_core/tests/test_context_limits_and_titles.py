# -*- coding: utf-8 -*-
"""Tester för kontexttak och sessionsnamn (chat-session-limits-and-names).

Två felklasser:

1. **En gräns som bara mäts i en dimension.** `_context_too_large` räknade
   bara tokens. En historik med tusentals korta verktygsrader kan ligga under
   tokenbudgeten men ändå nekas av providern — mätt 2026-11-05 med 2 121
   meddelanden som gav `400 Bad Request`.

2. **Ett namn som är en rubrik, inte ett innehåll.** Eftermälets `## Syfte`
   blev sessionsnamn eftersom `_clean_title` strippar markdown-rubriken men
   lämnar ordet kvar.
"""

from odoo.tests import common, tagged

from odoo.addons.ai_agent_core.core.loop import AgentConfig, AgentLoop


@tagged('post_install', '-at_install')
class TestMessageLimit(common.TransactionCase):
    """2.4: meddelandetaket prövas vid sidan av tokenbudgeten."""

    def _loop(self, **cfg):
        # AgentLoop kräver en ToolRegistry (den registrerar planeringsverktyg
        # i __init__), inte en lista.
        from odoo.addons.ai_agent_core.core.tools import ToolRegistry
        return AgentLoop(provider=None, tools=ToolRegistry(),
                         config=AgentConfig(**cfg))

    def _msgs(self, n, content='x'):
        from odoo.addons.ai_agent_core.core.provider import Message, Role
        return [Message(role=Role.USER, content=content) for _ in range(n)]

    def test_many_messages_trigger_summarization(self):
        """Fler meddelanden än taket ska räknas som för stor kontext.

        Fallerar på koden före rättningen: bara tokens prövades, och korta
        rader passerade.
        """
        loop = self._loop(max_messages=10, max_context_tokens=10_000_000)
        self.assertTrue(
            loop._context_too_large(self._msgs(20)),
            '20 meddelanden med tak 10 ska vara för stor kontext')

    def test_token_budget_still_applies(self):
        """Tokenbudgeten ska fortfarande prövas — båda taken gäller."""
        loop = self._loop(max_messages=1000, max_context_tokens=10)
        self.assertTrue(
            loop._context_too_large(self._msgs(5, content='x' * 100)),
            'tokens över budget ska vara för stor kontext')

    def test_normal_history_passes(self):
        """En historik under båda taken ska passera oförändrad."""
        loop = self._loop(max_messages=500, max_context_tokens=128_000)
        self.assertFalse(
            loop._context_too_large(self._msgs(10, content='hej')),
            'en liten historik ska inte trigga sammanfattning')

    def test_default_limit_is_measured(self):
        """Standardtaket ska ligga över p90 men under de nekade sessionerna."""
        cfg = AgentConfig()
        self.assertGreater(cfg.max_messages, 277,
                           'taket ska ligga över p90 (277 rader)')
        self.assertLess(cfg.max_messages, 2121,
                        'taket ska ligga under de 2 121 som nekades')


@tagged('post_install', '-at_install')
class TestSessionTitleQuality(common.TransactionCase):
    """3.3 och 4.3: rubrikord blir inte namn."""

    def test_heading_is_rejected(self):
        """`## Syfte` ska ge tomt namn, inte 'Syfte'."""
        S = self.env['ai.coworker.session']
        self.assertEqual(S._clean_title('## Syfte'), '')
        self.assertEqual(S._clean_title('Syfte'), '')
        self.assertEqual(S._clean_title('## Utfall'), '')
        self.assertEqual(S._clean_title('Beslut'), '')

    def test_heading_with_colon_is_stripped(self):
        """`Syfte: att testa` ska ge 'att testa'."""
        S = self.env['ai.coworker.session']
        self.assertEqual(S._clean_title('Syfte: att testa'), 'att testa')

    def test_word_containing_heading_passes(self):
        """Ett namn som INNEHÅLLER ett rubrikord ska passera."""
        S = self.env['ai.coworker.session']
        self.assertEqual(
            S._clean_title('Syftet med fakturan'), 'Syftet med fakturan')

    def test_heading_then_sentence_gives_sentence(self):
        """Eftermälets rubrik följd av en mening ska ge meningen.

        Det är `_title_from_summary` som plockar meningen — `_clean_title`
        tar bara första raden och ska ge tomt på en rubrik (D2/D3).
        """
        S = self.env['ai.coworker.session']
        summary = '## Syfte\nAnvändaren vill lista change requests'
        self.assertEqual(
            S._title_from_summary(summary),
            'Användaren vill lista change requests')
        # Och _clean_title ska INTE ta rubriken
        self.assertEqual(S._clean_title(summary), '')
