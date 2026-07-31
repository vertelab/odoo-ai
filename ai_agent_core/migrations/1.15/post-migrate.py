# -*- coding: utf-8 -*-
"""Migrate to 1.15: is_supervisor → orchestration_mode (change
ai-orchestration-tidy-up 6.5).

Legacy is_supervisor-flaggan fasas ut:
  - Records med is_supervisor=True och orchestration_mode i ('', 'single')
    får orchestration_mode='supervisor'.
  - Fältet är därefter readonly/deprecated (modelländring i samma release).

Idempotent: UPDATE träffar bara records som fortfarande matchar villkoret.
"""

import logging

_logger = logging.getLogger(__name__)


def _migrate_is_supervisor(cr):
    cr.execute("""
        UPDATE ai_coworker
        SET orchestration_mode = 'supervisor'
        WHERE is_supervisor
          AND (orchestration_mode IS NULL
               OR orchestration_mode = ''
               OR orchestration_mode = 'single')
    """)
    _logger.info('is_supervisor-migrering: %s coworker(s) → supervisor',
                 cr.rowcount)


def migrate(cr, version):
    try:
        _migrate_is_supervisor(cr)
    except Exception as e:
        _logger.warning('is_supervisor migration failed: %s', e)
