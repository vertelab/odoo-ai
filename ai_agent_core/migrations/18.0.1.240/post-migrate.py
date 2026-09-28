# -*- coding: utf-8 -*-
"""Migrate to 18.0.1.240: säkra search_vector-kolumnerna (BM25-sökvägen).

Bakgrund
--------
`ai_personal_memory` och `ai_company_memory` använder en GENERATED STORED
`search_vector` (tsvector, svensk stemming) för BM25-sökning. Kolumnen
skapas av `post_init_hook_personal_memory` i hooks.py.

BUGGEN (mätt i drift 2026-09-22)
--------------------------------
Manifestet registrerade `post_init_hook_personal_memory` som
`'post_init_hook'`. Odoo tillåter bara EN post_init_hook, så den
sammanhållande `post_init_hook` (som anropar `_ensure_default_model`,
`okf_ensure_search_infrastructure`, Quest/Skill Builder OCH
`post_init_hook_personal_memory`) kördes **aldrig**.

Följden i drift, databas `ledningssystem`:
  1. Kolumnen `search_vector` fanns inte på `ai_personal_memory`.
  2. BM25-sökningen slog i en icke-existerande kolumn:
       ERROR: column "search_vector" does not exist
  3. PostgreSQL förgiftade transaktionen (InFailedSqlTransaction).
  4. Varje efterföljande query i samma request dog, inklusive
     `session.exists()` i /ai/stream.
  5. HTTP 500 → användaren såg "Anslutningen till AI-servern bröts".

Reproducerat: session 21772 (13:06:16) och session 18178 (14:11:46) samma
dag — två olika användare, samma rot.

Vad migrationen gör
-------------------
Kör `post_init_hook_personal_memory`-logiken igen, men **utanför**
init-hooken så att den träffar redan installerade databaser. Funktionen är
idempotent (information_schema/pg_indexes-kontroller) och propagerar nu fel
i stället för att svälja dem.

Varför migration och inte bara hook-fixen
-----------------------------------------
`post_init_hook` körs bara vid `--init`. En redan installerad databas
uppgraderas med `--update`, som inte kör init-hooks. Utan denna migration
skulle kolumnen förbli saknad på alla existerande installationer.

Vad som INTE påverkas
---------------------
- Inga rader raderas eller skrivs om — endast DDL (ADD COLUMN + index).
- GENERATED-kolumnen beräknas av PostgreSQL ur `content`; ingen dataändring.
- Om kolumnen redan finns är hela migrationen en no-op.
"""

import logging

_logger = logging.getLogger(__name__)

# (tabell, kolumn, indexnamn) — samma mönster som hooks.py
_TARGETS = (
    ('ai_personal_memory', 'search_vector', 'idx_ai_personal_memory_fts'),
    ('ai_company_memory', 'search_vector', 'idx_ai_company_memory_fts'),
)


def _table_exists(cr, table):
    cr.execute(
        "SELECT 1 FROM information_schema.tables "
        "WHERE table_name = %s AND table_schema = 'public'",
        (table,))
    return cr.fetchone() is not None


def _column_exists(cr, table, column):
    cr.execute(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_name = %s AND column_name = %s",
        (table, column))
    return cr.fetchone() is not None


def _index_exists(cr, index):
    cr.execute(
        "SELECT 1 FROM pg_indexes WHERE indexname = %s", (index,))
    return cr.fetchone() is not None


def migrate(cr, version):
    for table, column, index in _TARGETS:
        if not _table_exists(cr, table):
            _logger.info(
                "%s: tabellen finns inte än — hoppar över", table)
            continue

        # 1. GENERATED STORED tsvector över content (svensk stemming)
        if not _column_exists(cr, table, column):
            cr.execute(
                "ALTER TABLE %s ADD COLUMN %s tsvector "
                "GENERATED ALWAYS AS "
                "(to_tsvector('swedish', coalesce(content, ''))) STORED"
                % (table, column))
            _logger.info("%s: skapade %s", table, column)
        else:
            _logger.info("%s: %s finns redan", table, column)

        # 2. GIN-index för BM25-sökningen
        if not _index_exists(cr, index):
            cr.execute(
                "CREATE INDEX %s ON %s USING GIN(%s)"
                % (index, table, column))
            _logger.info("%s: skapade GIN-index %s", table, index)
        else:
            _logger.info("%s: index %s finns redan", table, index)

    _logger.info(
        "18.0.1.240: search_vector-säkring klar (BM25-sökvägen)")
