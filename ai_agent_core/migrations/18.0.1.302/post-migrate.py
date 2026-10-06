# -*- coding: utf-8 -*-
"""Migrate to 18.0.1.302: UNIQUE-villkoret bär ägaren.

(okf-owner-and-access-scoping D6)

VARFÖR: villkoret var `UNIQUE(scope, concept_key, version)` — utan ägare.
Det gjorde att två ägare inte kunde ha samma `concept_key` och samma
`version`, vilket omöjliggjorde "en post -> N ägare" (t.ex. samma
kalenderhändelse för två deltagare: båda behöver version 1).

Villkoret blir:

    UNIQUE(scope, owner_company_id, owner_user_id,
           owner_coworker_id, concept_key, version)

Detta speglar exakt den gruppering `_okf_search`s DISTINCT ON och
`_latest_per_key()` använder efter ändringen — nyckeln och dedupen och
villkoret säger nu samma sak.

MIGRATIONEN ÄR IDEMPOTENT och rör INGEN data:
  - ingen ny kolumn (owner_* finns redan)
  - ingen omskrivning av rader
  - bara DROP + ADD av villkoret (ett index som redan finns)

Befintliga rader uppfyller alltid det nya villkoret: det är en
SUPERSET av det gamla (fler kolumner i nyckeln = svagare krav), så varje
rad som var unik förut är unik nu.

OBS: DROP/ADD tar ett ACCESS EXCLUSIVE-lås på tabellen en kort stund.
Kör i ett underhållsfönster om tabellen är stor (~40 000 rader i
ledningssystem 2026-10-06).
"""

import logging

_logger = logging.getLogger(__name__)

CONSTRAINT = 'ai_okf_concept_concept_key_scope_version_uniq'
TABLE = 'ai_okf_concept'


def migrate(cr, version):
    _logger.info("Running migration 18.0.1.302: UNIQUE-villkoret bär ägaren")

    # 1. Släpp det gamla villkoret om det finns. `IF EXISTS` gör körningen
    #    idempotent — en omskörd migration ska inte krascha.
    cr.execute(
        'ALTER TABLE %s DROP CONSTRAINT IF EXISTS %s' % (TABLE, CONSTRAINT),
    )

    # 2. Lägg det nya villkoret — men bara om det inte redan finns (t.ex.
    #    om Odoo redan hunnit skapa det via `_sql_constraints` vid
    #    moduluppdateringen, vilket är den normala vägen).
    cr.execute("""
        SELECT 1 FROM pg_constraint
         WHERE conname = %s AND conrelid = %s::regclass
    """, (CONSTRAINT, TABLE))
    if cr.fetchone():
        _logger.info(
            'OKF: villkoret %s finns redan — inget att göra', CONSTRAINT)
        return

    cr.execute("""
        ALTER TABLE %s
          ADD CONSTRAINT %s
          UNIQUE (scope, owner_company_id, owner_user_id,
                  owner_coworker_id, concept_key, version)
    """ % (TABLE, CONSTRAINT))

    _logger.info('OKF: villkoret %s byggt om med ägarkolumnerna', CONSTRAINT)
