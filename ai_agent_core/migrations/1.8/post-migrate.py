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
    #
    # `is_default` togs bort i 18.0.2.0.30. Kolumnen prövas innan den
    # läses, så migrationen är körbar även mot en DB där fältet redan är
    # släppt (och faller då tillbaka på xmlid).
    cr.execute(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_name = 'ai_coworker' AND column_name = 'is_default'"
    )
    has_flag = bool(cr.fetchone())
    try:
        if has_flag:
            already = env['ai.coworker'].search_count(
                [('is_default', '=', True)])
        else:
            already = env['ai.coworker'].search_count(
                [('id', '=', env.ref(
                    'ai_agent_core.coworker_default_assistent',
                    raise_if_not_found=False) or 0)])
        if not already:
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
