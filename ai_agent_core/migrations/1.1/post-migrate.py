# -*- coding: utf-8 -*-
"""Migration to 1.1: sync legacy is_supervisor to orchestration_mode."""

import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    _logger.info('Migrating ai.quest is_supervisor to orchestration_mode')
    cr.execute("""
        UPDATE ai_quest
        SET orchestration_mode = 'supervisor'
        WHERE is_supervisor = TRUE
          AND (orchestration_mode IS NULL OR orchestration_mode = 'single')
    """)
    _logger.info('Migrated %s quests to supervisor mode', cr.rowcount)
