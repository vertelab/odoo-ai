# -*- coding: utf-8 -*-
"""Efterfyll sökprofilen på medarbetare som skapades FÖRE fas 13.

Varför den här filen finns
--------------------------
Fas 13 byggde seedningen i `ai.coworker.create()` och `.write()`. Den kör
för NYA och ÄNDRADE rader. Alla 27 befintliga medarbetare i `social` hade
`search_strategy='balanced'` (fältets default, aldrig vald), `graph_enrichment
= NULL` och NOLL kopplade sökkällor — dvs funktionen var död i drift för
precis dem den skulle hjälpa.

Det är samma mönster som design.md §15/§16 beskriver: skrivsidan byggdes,
lässidan (den befintliga datan) antogs. Migrationen är den ärliga
återställningen: kör seedningen över det som redan finns.

Härdning (lärdom från migrations/1.11, som svalde sitt ALTER TABLE):
- Ingen bred try/except. Ett fel ska stoppa och synas.
- Idempotent: kör bara de rader som faktiskt saknar en koppling.
- Räknar och loggar vad den gjorde, så efterfyllnaden går att verifiera.
"""

import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    from odoo import api, SUPERUSER_ID

    env = api.Environment(cr, SUPERUSER_ID, {})
    Coworker = env['ai.coworker']

    Source = env['ai.search.source']
    Source._seed_sources()

    # Alla medarbetare UTAN kopplade källor. `search_strategy` rör vi inte:
    # den har ett fält-default ('balanced') och att skriva över ett medvetet
    # val är värre än att lämna en default. Källor och graf-fälten är däremot
    # tomma/NULL — de kan inte vara valda.
    #
    # M2M-relationstabellen är INTE en modell (`env[...]` → KeyError), så vi
    # läser den med SQL. Det felet såg ut som en krasch i migrationen — bra:
    # en tyst `except` hade gjort att efterfyllnaden aldrig körde och ingen
    # hade märkt något (exakt migrations/1.11:s fel).
    cr.execute('SELECT DISTINCT coworker_id '
               'FROM ai_coworker_search_source_rel')
    linked_ids = [row[0] for row in cr.fetchall()]

    orphan_domain = [('id', 'not in', linked_ids)] if linked_ids else []
    orphans = Coworker.search(orphan_domain)
    if not orphans:
        _logger.info('Sökprofil-backfill (18.0.1.208): inga orfa medarbetare.')
        return

    fixed = 0
    for c in orphans:
        vals = c._search_profile_presets(c.memory_profile or 'balanced')
        # Behåll en uttalad strategi om någon finns; annars profilens.
        # (Fältet har default 'balanced' = "inte vald", men ett verkligt
        # val ska inte skrivas över av en migration.)
        vals.pop('search_strategy', None)
        c.write(vals)
        fixed += 1

    # Räknas med SQL, inte med `Coworker.search(orphan_domain)`: domänen
    # byggdes mot listan av länkade ID:n FÖRE skrivningarna, så en
    # återanvändning av den ger samma siffra som före (loggen sa "27 kvar"
    # trots att alla 27 hade fått källor — en osanning i driftloggen).
    cr.execute('SELECT count(*) FROM ai_coworker c WHERE NOT EXISTS '
               '(SELECT 1 FROM ai_coworker_search_source_rel r '
               ' WHERE r.coworker_id = c.id)')
    remaining = cr.fetchone()[0]
    _logger.info(
        'Sökprofil-backfill (18.0.1.208): %d av %d medarbetare fick källor '
        'och sökprofil. Kvar utan källor: %d.',
        fixed, len(Coworker.search([])), remaining)
