# -*- coding: utf-8 -*-
"""Migrate to 1.6: data model changes for init-types-overhaul.

Handles:
- Populate channel_ids from legacy channel_id on ai.coworker.init_type
- Set default response_mode for existing chat/channel init_types
- Set default cron_interval_number/type for existing cron init_types
"""

import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    _logger.info("Running migration 1.6: init-types data model updates")

    # 1. Populate channel_ids from legacy channel_id
    cr.execute("""
        SELECT id, channel_id
        FROM ai_coworker_init_type
        WHERE channel_id IS NOT NULL
          AND init_type = 'channel'
    """)
    rows = cr.fetchall()
    for rec_id, channel_id in rows:
        # Check if this record already has the channel in channel_ids
        cr.execute("""
            SELECT COUNT(*)
            FROM ai_coworker_init_type_channel_rel
            WHERE init_type_id = %s AND channel_id = %s
        """, [rec_id, channel_id])
        if cr.fetchone()[0] == 0:
            cr.execute("""
                INSERT INTO ai_coworker_init_type_channel_rel (init_type_id, channel_id)
                VALUES (%s, %s)
            """, [rec_id, channel_id])
            _logger.info(
                "Migrated channel_id %s to channel_ids for init_type %s",
                channel_id, rec_id,
            )

    # 2. Set default response_mode for existing chat/channel records
    cr.execute("""
        UPDATE ai_coworker_init_type
        SET response_mode = 'mention'
        WHERE init_type IN ('chat', 'channel')
          AND response_mode IS NULL
    """)
    _logger.info("Set default response_mode for existing chat/channel records")

    # 3. Set default cron interval for existing cron records
    cr.execute("""
        UPDATE ai_coworker_init_type
        SET cron_interval_number = 1,
            cron_interval_type = 'hours'
        WHERE init_type = 'cron'
          AND cron_interval_number IS NULL
    """)
    _logger.info("Set default cron interval for existing cron records")

    _logger.info("Migration 1.6 complete")
