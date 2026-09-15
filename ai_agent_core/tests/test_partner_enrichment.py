# -*- coding: utf-8 -*-
"""Användarfall 0 — partner-berikning (external-agent-runtime §8).

Detta är det minsta användarfall som bevisar att runtime fungerar
FRISTÅENDE: ingen salt, ingen zabbix, ingen infrastruktur. Bara Odoo, en
LLM och ett web_search.

Testerna pinnar kontraktet:
- verktyget FÖRESLÅR utan att skriva (risk_level=read_only)
- skrivverktyget kräver godkännande (risk_level=write)
- coworkern är interaktiv OCH extern (runtime=external + server_action)
- ingen infrastruktur nämns någonstans i användarfall 0
"""

from odoo.tests import TransactionCase


class TestPartnerEnrichmentUseCase(TransactionCase):

    def setUp(self):
        super().setUp()
        self.partner = self.env['res.partner'].create({
            'name': 'Berikningstest AB',
            'is_company': True,
        })

    def _propose(self, **kw):
        tool = self.env.ref(
            'ai_agent_core.tool_partner_propose_enrichment')
        return tool._execute_tool(kw)

    def _apply(self, **kw):
        tool = self.env.ref('ai_agent_core.tool_partner_apply_enrichment')
        return tool._execute_tool(kw)

    # ── 8.2: förslaget skriver ingenting ─────────────────────────────

    def test_propose_tool_is_read_only(self):
        """Förslagsverktyget får inte kunna mutera partnern."""
        tool = self.env.ref(
            'ai_agent_core.tool_partner_propose_enrichment')
        self.assertEqual(tool.risk_level, 'read_only')

    def test_propose_does_not_write(self):
        """Att köra förslaget lämnar partnern orörd."""
        before = self.partner.website
        result = self._propose(partner_id=self.partner.id,
                               fields=['website', 'industry_id'])
        self.partner.invalidate_recordset()
        self.assertEqual(self.partner.website, before,
                         'förslaget får inte ha skrivit något')
        self.assertIn('website', result)
        self.assertIn('nuvarande värde', result)

    def test_propose_rejects_unknown_field(self):
        """Endast kända fält får föreslås."""
        result = self._propose(partner_id=self.partner.id,
                               fields=['password', 'website'])
        self.assertIn('website', result)
        self.assertNotIn('password', result)

    def test_propose_handles_missing_partner(self):
        """Ett okänt partner-id ger ett tydligt fel, inte ett krasch."""
        result = self._propose(partner_id=99999999)
        self.assertIn('Fel', result)

    # ── 8.3: skrivning kräver godkännande ────────────────────────────

    def test_apply_tool_requires_approval(self):
        """Skrivverktyget är risk_level=write → kräver HITL."""
        tool = self.env.ref('ai_agent_core.tool_partner_apply_enrichment')
        self.assertEqual(tool.risk_level, 'write')

    def test_apply_writes_only_allowed_fields(self):
        """Skrivning sker per fält, och bara för tillåtna fält."""
        result = self._apply(partner_id=self.partner.id,
                             field='website', value='https://example.com')
        self.partner.invalidate_recordset()
        self.assertEqual(self.partner.website, 'https://example.com')
        self.assertIn('Skrev website', result)

    def test_apply_refuses_non_writable_field(self):
        """Ett fält utanför listan skrivs aldrig."""
        result = self._apply(partner_id=self.partner.id,
                             field='name', value='Hackad AB')
        self.partner.invalidate_recordset()
        self.assertEqual(self.partner.name, 'Berikningstest AB')
        self.assertIn('Fel', result)

    # ── 8.1: coworkern är interaktiv OCH extern ──────────────────────

    def test_coworker_seed_exists(self):
        """Coworkern finns och är aktiv."""
        coworker = self.env.ref(
            'ai_agent_core.coworker_partner_enrichment')
        self.assertEqual(coworker.status, 'active')

    def test_agent_runs_external(self):
        """Agenten körs som en separat process (runtime=external)."""
        agent = self.env.ref('ai_agent_core.agent_partner_enrichment')
        self.assertEqual(agent.runtime, 'external')

    def test_coworker_is_interactive(self):
        """Init-typen är server_action → pausad HITL, ingen väntande worker."""
        coworker = self.env.ref(
            'ai_agent_core.coworker_partner_enrichment')
        active = coworker.init_type_ids.filtered('enabled')
        self.assertIn('server_action', active.mapped('init_type'))
        from odoo.addons.ai_agent_core.core.interrupt import (
            select_interrupt_handler, OpenAIInterruptHandler)
        handler = select_interrupt_handler('server_action')
        self.assertIsInstance(handler, OpenAIInterruptHandler)

    def test_agent_has_both_tools_attached(self):
        """Agenten har både förslags- och skrivverktyget."""
        agent = self.env.ref('ai_agent_core.agent_partner_enrichment')
        names = agent.tool_ids.mapped('name')
        self.assertIn('partner_propose_enrichment', names)
        self.assertIn('partner_apply_enrichment', names)

    # ── 8.6: ingen infrastruktur krävs ───────────────────────────────

    def test_use_case_needs_no_infrastructure(self):
        """Användarfall 0 nämner ingen infrastruktur — det bevisar att
        runtime fungerar fristående."""
        import inspect
        from odoo.addons.ai_agent_core.controllers import stream
        # Verktygens beskrivningar och kod får inte kräva salt/zabbix.
        for xmlid in ('tool_partner_propose_enrichment',
                      'tool_partner_apply_enrichment'):
            tool = self.env.ref('ai_agent_core.%s' % xmlid)
            blob = ((tool.description or '') + (tool.code or '')).lower()
            for forbidden in ('salt', 'zabbix', 'caddy', 'ssh', 'minion'):
                self.assertNotIn(
                    forbidden, blob,
                    '%s får inte kräva %s — användarfall 0 ska bevisa att '
                    'runtime fungerar utan infrastruktur' % (xmlid, forbidden))
