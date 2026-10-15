# -*- coding: utf-8 -*-
"""Pre-migration 18.0.1.328: bevara agent-koppling innan kolumnen släpps.

VARFÖR: org-goal-integrity D1 tar bort `agent_id` hårt från `ai.org.goal`.
När modellen inte längre deklarerar fältet släpper Odoo kolumnen under
`_auto_init` — och därmed försvinner den agent-koppling som fanns på
befintliga mål. För att ingen information ska tappas ohörbart kopieras
värdet in i `description` INNAN kolumnen släpps.

Detta är en PRE-migrering: den körs före modell-initieringen, medan
`agent_id`-kolumnen fortfarande finns. En post-migrering hade varit för
sen — kolumnen är borta när den körs.

Inventering (2026-10-15): drift (`ledningssystem` på postgres16.vertel.se)
hade 0 rader i `ai_org_goal` och 0 med `agent_id` satt. Migrationen är
alltså i praktiken en no-op i drift, men ligger kvar som skydd för varje
annan databas som uppgraderas med verklig data.

IDEMPOTENT: rader som redan har en anteckning i `description` lämnas
orörda. Saknas kolumnen (t.ex. vid en omskörd körning efter att Odoo
redan släppt den) görs ingenting.
"""

import logging

_logger = logging.getLogger(__name__)

NOTE_PREFIX = '[Migrerad agent-koppling] '


def migrate(cr, version):
    # Saknas kolumnen är det inget att bevara (redan släppt).
    cr.execute("""
        SELECT 1
          FROM information_schema.columns
         WHERE table_name = 'ai_org_goal'
           AND column_name = 'agent_id'
    """)
    if not cr.fetchone():
        _logger.info(
            'Pre-migration 18.0.1.328: ai_org_goal.agent_id saknas — '
            'inget att bevara')
        return

    # Koppla agent-id → agent-namn och appenda en anteckning i description.
    # Korrelerad subquery (inte FROM ... WHERE g.agent_id = a.id) så att även
    # en föräldralös agent_id (agenten borta) bevaras som '#<id>' — annars
    # tappas just de raderna ohörbart. COALESCE + NULLIF behåller en
    # befintlig description; rader utan agent_id lämnas orörda.
    cr.execute("""
        UPDATE ai_org_goal AS g
           SET description = COALESCE(NULLIF(g.description, ''), '')
                             || %s
                             || COALESCE(
                                    (SELECT a.name FROM ai_agent a
                                      WHERE a.id = g.agent_id),
                                    '#' || g.agent_id::text)
         WHERE g.agent_id IS NOT NULL
    """, (NOTE_PREFIX,))
    updated = cr.rowcount
    _logger.info(
        'Pre-migration 18.0.1.328: bevarade agent-koppling på %s mål',
        updated)
