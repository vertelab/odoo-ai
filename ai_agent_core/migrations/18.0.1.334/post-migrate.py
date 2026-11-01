# -*- coding: utf-8 -*-
"""Migrate to 18.0.1.334: städa dubbletter på (session_id, sequence).

VARFÖR: `ai_coworker_session_line` saknade ett unikhetsvillkor på
`(session_id, sequence)`. Sekvensen sätts av `_next_session_sequence`, som
läser max och skriver max+1 — en läs-och-skriv-cykel utan lås. Två samtidiga
skrivningar i samma session fick därför samma sekvens, och historiken kunde
inte sorteras deterministiskt (dubblettraden i session 18204).

Den här migrationen MÅSTE köra FÖRE constrainten läggs på — annars faller
`ALTER TABLE ... ADD CONSTRAINT` på befintliga dubbletter och hela
uppgraderingen rullas tillbaka.

Strategi: behåll raden med LÄGST `id` på sin sekvens (den skrevs först, och
`_order = 'session_id, sequence asc'` sorterar redan stabilt på id som
tiebreak). Flytta övriga till nya sekvenser OVANFÖR sessionens max, i
`id`-ordning, så att den relativa ordningen bevaras.

IDEMPOTENT: körs den igen finns inga dubbletter kvar och ingenting händer.
"""

import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return

    cr.execute("""
        SELECT session_id, sequence, COUNT(*) AS n
          FROM ai_coworker_session_line
      GROUP BY session_id, sequence
        HAVING COUNT(*) > 1
      ORDER BY session_id, sequence
    """)
    duplicates = cr.fetchall()
    if not duplicates:
        _logger.info(
            '18.0.1.334: inga dubbletter på (session_id, sequence) — '
            'constraint kan läggas på direkt')
        return

    _logger.warning(
        '18.0.1.334: %d dubblett(er) på (session_id, sequence) hittade — '
        'flyttar överskjutande rader ovanför sessionens max',
        len(duplicates))

    moved_total = 0
    for session_id, sequence, count in duplicates:
        # Alla rader på den kolliderande sekvensen, lägst id först.
        cr.execute("""
            SELECT id FROM ai_coworker_session_line
             WHERE session_id = %s AND sequence = %s
          ORDER BY id ASC
        """, (session_id, sequence))
        ids = [row[0] for row in cr.fetchall()]
        if len(ids) < 2:
            continue
        keep, move = ids[0], ids[1:]

        # Sessionens nuvarande max — flyttade rader hamnar ovanför.
        cr.execute("""
            SELECT COALESCE(MAX(sequence), 0)
              FROM ai_coworker_session_line
             WHERE session_id = %s
        """, (session_id,))
        next_seq = (cr.fetchone() or [0])[0]

        for line_id in move:
            next_seq += 1
            cr.execute("""
                UPDATE ai_coworker_session_line
                   SET sequence = %s
                 WHERE id = %s
            """, (next_seq, line_id))
            _logger.info(
                '18.0.1.334: session %s rad %s flyttad %s → %s '
                '(behöll rad %s på %s)',
                session_id, line_id, sequence, next_seq, keep, sequence)
            moved_total += 1

    _logger.warning(
        '18.0.1.334: %d rad(er) flyttade — dubbletterna är städade',
        moved_total)
