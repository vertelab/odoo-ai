# -*- coding: utf-8 -*-
"""Pre-migration 18.0.2.0.23: content_hash + städning av persisteringsdubbletter.

VARFÖR (pi-message-persist-dedup): Pi skickar hela sin `messages[]` vid varje
anrop. Odoo persisterade deltat via kursorn `pi_message_count`, som antar att
listan växer monotont. Pi kompakterar historiken — då är listan kortare,
kursorn pekar förbi, och hela historiken skrevs om.

Mätt i drift 2026-10-20:

    session 24154       34 866 rader, 877 user-rader (72 unika)
                        115 user-rader med sequence=1 (11 unika)
    alla sessioner      224 985 identiska rader utan token-bokföring

TVÅ SORTERS UPPREPNING — bara den första städas:

    persisteringsdubbletter   224 985 rader   token_sys-summa 0
      → äkta dubbletter, raderas här

    retry-anrop                57 357 rader   1 362 659 924 token_sys
      → samma text men OLIKA tokenantal (98 066 / 98 325 / 98 557 med 5 s
        mellanrum) = separata API-anrop som kostade pengar
      → BEVARAS, de är inte dubbletter

VARFÖR PRE: kolumnen måste finnas och vara ifylld innan indexet skapas och
innan `_auto_init` lägger sin egen kolumn.

IDEMPOTENT: `ADD COLUMN IF NOT EXISTS`, backfillen rör bara NULL-rader,
städningen tar bara rader som fortfarande är dubbletter.

BATCHAT: 50 000 rader per sats så tabellen inte låses under hela körningen.
"""

import logging

_logger = logging.getLogger(__name__)

BATCH_SIZE = 50_000

HASH_EXPR = (
    "md5(coalesce(role,'') || '|' || coalesce(tool_call_id,'') || '|' "
    "|| coalesce(content,''))"
)


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


def _backfill_hash(cr):
    """Fyll content_hash på befintliga rader, i batchar."""
    total = 0
    while True:
        cr.execute(
            """
            UPDATE ai_coworker_session_line
               SET content_hash = %s
             WHERE id IN (
                 SELECT id FROM ai_coworker_session_line
                  WHERE content_hash IS NULL
                  LIMIT %s
             )
            """ % (HASH_EXPR, '%s'),
            (BATCH_SIZE,),
        )
        updated = cr.rowcount or 0
        total += updated
        if updated < BATCH_SIZE:
            break
    return total


def _delete_duplicates(cr):
    """Radera identiska rader som saknar token-bokföring.

    Behåller lägsta id i varje grupp. Rader med `token_input` eller
    `token_output` rörs aldrig — de bär ett faktiskt anrop (retry-loopen).
    """
    total = 0
    while True:
        cr.execute(
            """
            DELETE FROM ai_coworker_session_line
             WHERE id IN (
                 SELECT id FROM (
                     SELECT id,
                            row_number() OVER (
                                PARTITION BY session_id, content_hash
                                ORDER BY id
                            ) AS rn
                       FROM ai_coworker_session_line
                      WHERE coalesce(token_input, 0) = 0
                        AND coalesce(token_output, 0) = 0
                        AND content_hash IS NOT NULL
                 ) dup
                 WHERE dup.rn > 1
                 LIMIT %s
             )
            """,
            (BATCH_SIZE,),
        )
        deleted = cr.rowcount or 0
        total += deleted
        if deleted < BATCH_SIZE:
            break
    return total


def migrate(cr, version):
    if not _table_exists(cr, 'ai_coworker_session_line'):
        _logger.info(
            'Pre-migration 18.0.2.0.23: ai_coworker_session_line saknas — '
            'inget att migrera')
        return

    # ── 1. content_hash ────────────────────────────────────────────────
    if not _column_exists(cr, 'ai_coworker_session_line', 'content_hash'):
        cr.execute(
            'ALTER TABLE ai_coworker_session_line '
            'ADD COLUMN IF NOT EXISTS content_hash varchar(32)')
    filled = _backfill_hash(cr)
    if filled:
        _logger.info(
            'Pre-migration 18.0.2.0.23: content_hash ifyllt på %s rader',
            filled)

    # ── 2. Index för dedup-uppslagningen ───────────────────────────────
    cr.execute(
        'CREATE INDEX IF NOT EXISTS '
        'ai_coworker_session_line_session_hash_idx '
        'ON ai_coworker_session_line (session_id, content_hash)')

    # ── 3. Städning av persisteringsdubbletter ─────────────────────────
    deleted = _delete_duplicates(cr)
    if deleted:
        _logger.info(
            'Pre-migration 18.0.2.0.23: %s persisteringsdubbletter raderade '
            '(rader med tokens orörda)', deleted)

    _logger.info(
        'Pre-migration 18.0.2.0.23 klar: content_hash, index och städning')
