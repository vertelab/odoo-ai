# -*- coding: utf-8 -*-
"""Pre-migration 18.0.1.335: index, lagrad coworker och NOT NULL.

VARFÖR (coworker-form-performance): formuläret för en AI Medarbetare tog
27 s att öppna för den största medarbetaren (225 774 session-rader). Tre
orsaker i schemat:

1. `ai_coworker_session_line` saknade index på `session_id` och
   `create_date`, och `ai_coworker_session` saknade index på `coworker_id`
   och `user_id`. Månadssumman valde i stället två-värdes-indexet på
   `informative` och läste alla rader (mätt: 186 ms, 15 912 bortfiltrerade).
2. `coworker_id` fanns inte på session-raden, så varje budgetfråga måste
   joina sessionen och depends-kedjan blev två led
   (`session_ids.session_line_ids`).
3. `informative` var nullable. Python räknade NULL som false, SQL
   exkluderade dem — två semantiker för samma fråga (142 229 rader,
   2026-08-05 → 09-24, 1,55 miljarder systemtokens).

VARFÖR PRE: kolumnen `coworker_id` måste finnas och vara ifylld INNAN
Odoo:s `_auto_init` skapar det lagrade related-fältet och indexet på det.
En post-migrering hade varit för sen — `_auto_init` hade redan lagt
kolumnen (NULL) och indexet.

IDEMPOTENT: `ADD COLUMN IF NOT EXISTS`, `CREATE INDEX IF NOT EXISTS`,
backfillen rör bara NULL-rader, och `SET NOT NULL`/`SET DEFAULT` är no-ops
om de redan gäller. En omskörd körning gör ingenting.

BATCHAT: backfillen av `coworker_id` sker i satser om 50 000 rader så att
tabellen inte låses under hela uppdateringen (mätt: ~2–5 s totalt för
493 608 rader).
"""

import logging

_logger = logging.getLogger(__name__)

BATCH_SIZE = 50_000


def _table_exists(cr, table):
    cr.execute(
        """
        SELECT 1
          FROM information_schema.tables
         WHERE table_schema = 'public' AND table_name = %s
        """,
        (table,),
    )
    return bool(cr.fetchone())


def _column_exists(cr, table, column):
    cr.execute(
        """
        SELECT 1
          FROM information_schema.columns
         WHERE table_schema = 'public' AND table_name = %s
           AND column_name = %s
        """,
        (table, column),
    )
    return bool(cr.fetchone())


def _create_index(cr, name, table, columns):
    cr.execute(
        'CREATE INDEX IF NOT EXISTS %s ON %s %s' % (name, table, columns))


def _backfill_coworker_id(cr):
    """Kopiera sessionens coworker_id till raden, i batchar."""
    total = 0
    while True:
        cr.execute(
            """
            UPDATE ai_coworker_session_line AS line
               SET coworker_id = session.coworker_id
              FROM ai_coworker_session AS session
             WHERE line.session_id = session.id
               AND line.coworker_id IS NULL
               AND session.coworker_id IS NOT NULL
               AND line.id IN (
                   SELECT id
                     FROM ai_coworker_session_line
                    WHERE coworker_id IS NULL
                    LIMIT %s
               )
            """,
            (BATCH_SIZE,),
        )
        updated = cr.rowcount or 0
        total += updated
        if updated < BATCH_SIZE:
            break
    return total


def _backfill_informative(cr):
    """NULL → false, i batchar (kolumnen blir NOT NULL strax efter)."""
    total = 0
    while True:
        cr.execute(
            """
            UPDATE ai_coworker_session_line
               SET informative = false
             WHERE id IN (
                 SELECT id
                   FROM ai_coworker_session_line
                  WHERE informative IS NULL
                  LIMIT %s
             )
            """,
            (BATCH_SIZE,),
        )
        updated = cr.rowcount or 0
        total += updated
        if updated < BATCH_SIZE:
            break
    return total


def migrate(cr, version):
    if not _table_exists(cr, 'ai_coworker_session_line'):
        _logger.info(
            'Pre-migration 18.0.1.335: ai_coworker_session_line saknas — '
            'inget att migrera')
        return

    # ── 1. coworker_id på session-raden ────────────────────────────────
    if not _column_exists(cr, 'ai_coworker_session_line', 'coworker_id'):
        cr.execute(
            'ALTER TABLE ai_coworker_session_line '
            'ADD COLUMN IF NOT EXISTS coworker_id integer')
    filled = _backfill_coworker_id(cr)
    if filled:
        _logger.info(
            'Pre-migration 18.0.1.335: coworker_id ifyllt på %s rader',
            filled)

    # ── 2. informative: NULL → false, därefter NOT NULL ────────────────
    normalized = _backfill_informative(cr)
    if normalized:
        _logger.info(
            'Pre-migration 18.0.1.335: informative NULL → false på %s rader',
            normalized)
    cr.execute(
        'ALTER TABLE ai_coworker_session_line '
        'ALTER COLUMN informative SET DEFAULT false')
    cr.execute(
        'ALTER TABLE ai_coworker_session_line '
        'ALTER COLUMN informative SET NOT NULL')

    # ── 3. Index (samma som _auto_init skapar på en färsk databas) ─────
    _create_index(
        cr, 'ai_coworker_session_line_session_id_idx',
        'ai_coworker_session_line', '(session_id)')
    _create_index(
        cr, 'ai_coworker_session_line_create_date_idx',
        'ai_coworker_session_line', '(create_date)')
    _create_index(
        cr, 'ai_coworker_session_line_coworker_create_idx',
        'ai_coworker_session_line', '(coworker_id, create_date)')

    if _table_exists(cr, 'ai_coworker_session'):
        _create_index(
            cr, 'ai_coworker_session_coworker_id_idx',
            'ai_coworker_session', '(coworker_id)')
        _create_index(
            cr, 'ai_coworker_session_user_id_idx',
            'ai_coworker_session', '(user_id)')
        _create_index(
            cr, 'ai_coworker_session_coworker_create_idx',
            'ai_coworker_session', '(coworker_id, create_date)')

    _logger.info(
        'Pre-migration 18.0.1.335 klar: index på session-rad och session, '
        'coworker_id lagrad, informative NOT NULL')
