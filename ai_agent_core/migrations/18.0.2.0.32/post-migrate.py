# -*- coding: utf-8 -*-
"""Post-migration 18.0.2.0.32: städa den döda default_provider-parametern.

VARFÖR: `ir.config_parameter ai_agent_core.default_provider_id` skrevs av
fältet `res.config.settings.ai_api_default_provider_id` men lästes ALDRIG av
någon kod — en död parameter i UI:t. Fältet är nu borttaget
(default-coworker-som-provider 2.1), och parametern ska inte ligga kvar som
en vilseledande rest i Technical → System Parameters.

VIKTIGT — ingen dataflytt här: default-COWORKERN flyttades till
`res.company.ai_default_coworker_id` redan i
`18.0.2.0.31/pre-migrate.py` (där `ai_coworker.is_default`-kolumnen ännu
fanns). Den här migrationen rör bara den döda provider-parametern.

IDEMPOTENT: raderar bara om raden finns; loggar vad som hände (antal rader,
inte bara "klart").
"""

import logging

_logger = logging.getLogger(__name__)

DEAD_KEY = 'ai_agent_core.default_provider_id'


def migrate(cr, version):
    if not version:
        # Färsk installation — inget att städa.
        return

    cr.execute(
        "DELETE FROM ir_config_parameter WHERE key = %s", (DEAD_KEY,))
    removed = cr.rowcount

    if removed:
        _logger.info(
            'Post-migration 18.0.2.0.32: tog bort %s död parameter (%s) — '
            'den skrevs men lästes aldrig av någon kod',
            removed, DEAD_KEY)
    else:
        _logger.info(
            'Post-migration 18.0.2.0.32: %s fanns inte — inget att städa',
            DEAD_KEY)

    # Verifiera att default-COWORKERN fortfarande är satt där den ska vara.
    # (Flytten gjordes i 18.0.2.0.31/pre-migrate.py; detta är en kontroll,
    # inte en åtgärd — vi gissar aldrig ett värde här.)
    cr.execute("""
        SELECT COUNT(*) FROM res_company
         WHERE ai_default_coworker_id IS NOT NULL
    """)
    with_default = cr.fetchone()[0]
    cr.execute("SELECT COUNT(*) FROM res_company")
    total = cr.fetchone()[0]
    _logger.info(
        'Post-migration 18.0.2.0.32: %s av %s bolag har ett '
        'default-coworker-val', with_default, total)
