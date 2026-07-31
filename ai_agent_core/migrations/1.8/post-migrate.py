# -*- coding: utf-8 -*-
"""Migrate to 1.8: AI Organization — default coworker + templates.

Creates the default "Allmän" coworker + agent (if missing) and
loads the organization templates from data/templates/*.json.
"""

import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    from odoo.api import Environment, SUPERUSER_ID
    env = Environment(cr, SUPERUSER_ID, {})

    _logger.info("Running migration 1.8: AI Organization init")

    # ── 1. Create default coworker if missing ──
    try:
        if not env['ai.coworker'].search_count([('is_default', '=', True)]):
            agent = env['ai.agent'].create({
                'name': 'Allmän assistent',
                'ai_role': 'General purpose AI assistant',
                'status': 'active',
            })
            coworker = env['ai.coworker'].create({
                'name': 'Allmän',
                'description': 'Allmän AI-assistent. Hjälper med frågor, '
                              'styr upp organisationen, och tipsar om '
                              'förbättringar via kaizen.',
                'init_type': 'manual',
                'status': 'active',
                'heartbeat_enabled': True,
                'inject_company_memory': True,
                'inject_nudging': True,
                'is_default': True,
            })
            env['ai.coworker.agent'].create({
                'coworker_id': coworker.id,
                'agent_id': agent.id,
                'role': 'lead',
            })
            InitType = env['ai.coworker.init_type']
            InitType.create({
                'coworker_id': coworker.id,
                'init_type': 'web_ui',
                'active': True,
            })
            InitType.create({
                'coworker_id': coworker.id,
                'init_type': 'cron',
                'active': True,
                'cron_interval_number': 5,
                'cron_interval_type': 'minutes',
            })
            env.flush_all()
            _logger.info('Created default coworker: Allmän')
        else:
            _logger.info('Default coworker already exists — skipping')
    except Exception as e:
        _logger.warning('Default coworker creation failed (non-fatal): %s', e)

    # ── 2. Load templates from JSON files ──
    try:
        if 'ai.org.template' in env:
            env['ai.org.template'].load_all_templates()
            env.flush_all()
            _logger.info('Templates loaded')
    except Exception as e:
        _logger.warning('Template loading failed (non-fatal): %s', e)
