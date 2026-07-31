# -*- coding: utf-8 -*-
"""AI Organization Bridge Protocol — abstrakt protokoll för externa moduler.

Bridge-moduler (t.ex. ai_agent_strategy, ai_agent_marketing) ärver
denna modell och implementerar metoderna för att kommunicera med
ai_agent_core.
"""

import logging
from odoo import models, api, _

_logger = logging.getLogger(__name__)


class AIOrgBridge(models.AbstractModel):
    _name = 'ai.org.bridge'
    _description = 'AI Organization Bridge Protocol'

    # ── Registration ──

    @api.model
    def _get_domain_name(self):
        """Return unique domain name, e.g. 'strategy', 'marketing'.

        Must be implemented by each bridge.
        """
        raise NotImplementedError(
            _('Bridge must implement _get_domain_name()'))

    @api.model
    def _get_external_ref_models(self):
        """Return list of (model_name, description) for Reference fields.

        E.g. [('strategy.initiative', 'Strategy Initiative')]
        """
        return []

    # ── Inbound: External Module → Core ──

    @api.model
    def fetch_executive_summary(self):
        """Generate executive summary from external module data.

        Returns dict or None:
            content (str): summary text
            category (str): 'mgmt_summary.{domain}'
            scope (str): 'public' or 'restricted'
            importance (str): 'high', 'medium', 'low'
        """
        return {}

    @api.model
    def sync_goals_to_core(self):
        """Push external goals/OKRs into ai.org.goal.

        Returns list of created/updated goal IDs.
        """
        return []

    # ── Outbound: Core → External Module ──

    @api.model
    def execute_instruction(self, instruction):
        """Execute an AI agent's instruction in the external domain.

        instruction dict:
            action (str): what to do
            params (dict): parameters
            source (str): which agent/coworker

        Must be implemented by each bridge.
        """
        raise NotImplementedError(
            _('Bridge must implement execute_instruction()'))

    # ── Core: cross-bridge helpers ──

    @api.model
    def collect_all_summaries(self):
        """Samla executive summaries från alla installerade bridges."""
        summaries = []
        bridges = self.env['ai.org.bridge'].search([])
        for bridge in bridges:
            try:
                summary = bridge.fetch_executive_summary()
                if summary and summary.get('content'):
                    summaries.append(summary)
                    # Store as company memory
                    self.env['ai.company.memory'].create({
                        'content': summary['content'],
                        'category': summary.get(
                            'category',
                            f'mgmt_summary.{bridge._get_domain_name()}'),
                        'scope': summary.get('scope', 'public'),
                        'importance': summary.get('importance', 'medium'),
                    })
                    _logger.info(
                        'Bridge %s: executive summary stored',
                        bridge._get_domain_name())
            except Exception as e:
                _logger.warning(
                    'Bridge %s summary failed: %s',
                    bridge._get_domain_name(), e)
        return summaries

    @api.model
    def dispatch_instruction(self, domain, instruction):
        """Skicka instruktion till rätt bridge."""
        bridges = self.env['ai.org.bridge'].search([])
        for bridge in bridges:
            if bridge._get_domain_name() == domain:
                return bridge.execute_instruction(instruction)
        raise ValueError(
            _('No bridge found for domain: %s') % domain)
