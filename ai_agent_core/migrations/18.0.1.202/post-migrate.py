# -*- coding: utf-8 -*-
"""Migrate to 18.0.1.202: OKF-sökvägen — search_vector + index (okf-recall-path fas 11).

Bakgrund: migration 1.11 försökte skapa `search_vector` på `ai_okf_concept`,
men körde innan ORM:en skapat tabellen. Varje `ALTER TABLE` slog i en
icke-existerande tabell och svaldes av `except: _logger.warning('non-fatal')`.
Loggen påstod "Created search_vector on ai_okf_concept" — kolumnen uppstod
aldrig. Verifierat i drift (`social`): varken `search_vector`, GIN-indexet,
ivfflat-indexet eller B-tree-indexet fanns; endast ORM:ens egen pkey.

Denna migration kör efter tabellskapandet och anropar SAMMA funktion som
post_init_hook — en implementation, två vägar in.

Fel PROPAGERAS medvetet: en tyst tom sökväg är värre än ett högljutt fel
(krav: "Migrationen får inte tystna").
"""

import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    from odoo.api import Environment, SUPERUSER_ID
    from odoo.addons.ai_agent_core.hooks import okf_ensure_search_infrastructure

    _logger.info('Running migration 18.0.1.202: OKF search_vector-infrastruktur')

    env = Environment(cr, SUPERUSER_ID, {})
    okf_ensure_search_infrastructure(env)

    # Verifiera resultatet — migrationen ska inte kunna rapportera framgång
    # utan att kolumnen faktiskt finns (det var precis vad 1.11 gjorde).
    cr.execute("""
        SELECT column_name, data_type FROM information_schema.columns
        WHERE table_name = 'ai_okf_concept' AND column_name = 'search_vector'
    """)
    row = cr.fetchone()
    if not row or row[1] != 'tsvector':
        raise Exception(
            'OKF search_vector saknas efter migration 18.0.1.202 — '
            'kolumnen skulle ha skapats men verifieringen misslyckades'
        )

    cr.execute("""
        SELECT 1 FROM pg_indexes
        WHERE tablename = 'ai_okf_concept'
          AND indexname = 'idx_ai_okf_concept_fts'
    """)
    if not cr.fetchone():
        raise Exception(
            'OKF GIN-index saknas efter migration 18.0.1.202'
        )

    _logger.info('OKF search_vector + GIN-index verifierade i databasen')
