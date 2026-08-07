# -*- coding: utf-8 -*-
"""Migrate to 18.0.1.112: datadrivna provider-flaggor + provider-rename.

1. Sätter is_bifrost/api_style på befintliga ai.provider-records utifrån
   provider_type (mappning speglar models/ai_provider.py::_flags_from_type).
2. ai.model: kopierar provider_id → provider (oldname stöds inte i detta
   Odoo-bygge; post-migrate körs efter schema-uppdateringen så kolumnen
   `provider` finns) och släpper den gamla kolumnen.
"""

import logging

_logger = logging.getLogger(__name__)


def _flags_from_type(provider_type):
    """Samma mapping som models/ai_provider.py — håll synkad."""
    type_lower = (provider_type or '').lower()
    if type_lower == 'bifrost':
        return True, 'openai'
    if type_lower == 'anthropic':
        return False, 'anthropic'
    return False, 'openai'


def migrate(cr, version):
    _logger.info("Running migration 18.0.1.112: datadrivna provider-flaggor + rename")

    # 1. Provider-flaggor på ai.provider
    cr.execute("SELECT id, provider_type FROM ai_provider")
    rows = cr.fetchall()
    updated = 0
    for pid, ptype in rows:
        is_bifrost, api_style = _flags_from_type(ptype)
        cr.execute("""
            UPDATE ai_provider
               SET is_bifrost = %s,
                   api_style = %s
             WHERE id = %s
        """, (is_bifrost, api_style, pid))
        updated += 1
    _logger.info("Migration 18.0.1.112: %d ai.provider-records uppdaterade", updated)

    # 2. ai.model: provider_id → provider (datakopiering + drop)
    cr.execute("""
        UPDATE ai_model
           SET provider = provider_id
         WHERE provider IS NULL
           AND provider_id IS NOT NULL
    """)
    _logger.info("Migration 18.0.1.112: %d ai.model-rader kopierade provider_id → provider",
                 cr.rowcount)
    cr.execute("ALTER TABLE ai_model DROP COLUMN IF EXISTS provider_id")
    _logger.info("Migration 18.0.1.112: gammal kolumn provider_id släppt")
