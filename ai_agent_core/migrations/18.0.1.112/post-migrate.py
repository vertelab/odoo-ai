# -*- coding: utf-8 -*-
"""Migrate to 18.0.1.112: datadrivna provider-flaggor (fix-provider-resolution).

Sätter is_bifrost/api_style på befintliga ai.provider-records utifrån
provider_type, så att den enda provider-klassen (AIProvider) får rätt auth
utan hårdkodning. Mappningen speglar models/ai_provider.py::_flags_from_type
— håll synkad vid ändring.
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
    _logger.info("Running migration 18.0.1.112: datadrivna provider-flaggor")

    cr.execute("SELECT id, provider_type FROM ai_provider")
    rows = cr.fetchall()
    if not rows:
        _logger.info("Migration 18.0.1.112: inga ai.provider-records — klart")
        return

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
    _logger.info("Migration 18.0.1.112: %d provider-records uppdaterade", updated)
