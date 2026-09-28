# -*- coding: utf-8 -*-
"""Migrate to 18.0.1.204: OKF-bryggan för legacy-minnena (okf-recall-path fas 6, D7).

Bakgrund: `okf_dirty` fanns bara på `ai.memory`. De två legacy-modellerna
`ai.personal.memory` och `ai.company.memory` saknade flaggan helt — de kunde
alltså skrivas hur mycket som helst utan att någonsin bli OKF-koncept.
Det är den sista biten av migreringen: skrivsidan har flyttats, men en post
som skapas i legacy-stacken stannar där för alltid.

`ai.personal.memory` och `ai.company.memory` är båda tomma i drift just nu
(0 rader), så `DEFAULT TRUE` kostar ingenting — men den är rätt: hade det
funnits rader skulle de behöva indexeras.

Kolumnerna läggs av ORM:en eftersom fälten deklareras på mixin
(`ai.memory.mixin`), som båda modellerna ärver. Denna migration verifierar
bara att resultatet blev rätt, och är därför säker att köra även om Odoo
hunnit skapa kolumnerna först (idempotent).
"""

import logging

_logger = logging.getLogger(__name__)

LEGACY_TABLES = ('ai_personal_memory', 'ai_company_memory')


def migrate(cr, version):
    _logger.info('Running migration 18.0.1.204: OKF-bryggan för legacy-minnen')

    for table in LEGACY_TABLES:
        cr.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = %s AND column_name IN ('okf_dirty', 'okf_indexed_at')
        """, (table,))
        found = {row[0] for row in cr.fetchall()}
        missing = {'okf_dirty', 'okf_indexed_at'} - found
        if missing:
            # Odoo ska ha skapat dem (fälten finns på mixin). Om de inte
            # finns är det ett fel i modellregistreringen som ska märkas nu,
            # inte tyst upptäckas när cronen kör `UPDATE` mot en okänd kolumn.
            raise Exception(
                'OKF-bryggan: kolumnerna %s saknas på %s efter migration '
                '18.0.1.204 — fälten deklareras på ai.memory.mixin'
                % (sorted(missing), table)
            )

        # Poster som ännu inte indexerats ska plockas upp av cronen. Gör
        # ingen skillnad för tomma tabeller; rätt sak för icke-tomma.
        cr.execute(
            "UPDATE %s SET okf_dirty = TRUE "
            "WHERE okf_dirty IS NULL OR okf_indexed_at IS NULL" % table
        )
        updated = cr.rowcount
        if updated:
            _logger.info('OKF-bryggan: %s poster i %s väntar på indexering',
                         updated, table)

    _logger.info('OKF-bryggan verifierad på %s', ', '.join(LEGACY_TABLES))
