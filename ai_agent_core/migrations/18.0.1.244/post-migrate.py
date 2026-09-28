# -*- coding: utf-8 -*-
"""Migrate to 18.0.1.244: embedding text → vector(1024) på legacy-minnena.

BAKGRUND
--------
`ai_personal_memory` och `ai_company_memory` deklarerar båda
`embedding = fields.Text(...)`, så Odoo skapar en TEXT-kolumn. Endast
`ai_okf_concept.embedding` använder `PgVector` (fields/pg_vector.py).

Den semantiska sökvägen kör emellertid:

    1 - (embedding <=> %s::vector)

vilket är ett SQL-fel på en TEXT-kolumn:

    psycopg2.errors.UndefinedFunction:
      operator does not exist: text <=> vector

BUGGEN (mätt i drift 2026-09-22, databas `ledningssystem`)
----------------------------------------------------------
`hooks.py` gjorde `ALTER COLUMN embedding TYPE vector` **bara om**
`data_type == 'USER-DEFINED'` — dvs. bara om kolumnen redan var en
vector-typ. En TEXT-kolumn lämnades som text. Cirkulär logik: den
migreras bara om den redan är migrerad.

`migrations/1.10` och `1.11` gjorde samma sak, men bara för
`ai_okf_concept` — legacy-tabellerna rördes aldrig.

FÖLJDEN I DRIFT
---------------
1. `/ai/stream` med en session som har >50 rader anropar
   `_summarize_history` (stream.py:374).
2. → `_write_final_summary` → `_bridge_to_personal_memory`
   (ai_session.py:1264).
3. → `ai.personal.memory.extract_from_session` → `search_for_user`
   → den semantiska SQL:en ovan.
4. Felet låg INTE i en savepoint → PostgreSQL förgiftade hela
   transaktionen (`InFailedSqlTransaction`).
5. Varje efterföljande query i requesten dog — inkl. `quest.exists()`
   (stream.py:435) → HTTP 500.
6. Användaren såg "❌ Anslutningen till AI-servern bröts — försök igen."

Reproducerat: session 22126 (315 rader) gav HTTP 500 på 10,8 s; samma
anrop utan `session_id` gav 200 på 3,8 s.

VAD MIGRATIONEN GÖR
-------------------
Konverterar `embedding` till `vector(1024)` på båda legacy-tabellerna,
förutsatt att `vector`-extensionen finns. Värden som redan skrivits som
text-literaler ('[0.1,0.2,...]') är giltiga vector-literaler och castas
direkt; NULL förblir NULL. Otolkbara värden fäller inte hela ALTER:en —
de blir NULL och den semantiska signalen hoppar över dem.

VARFÖR MIGRATION OCH INTE BARA HOOK-FIXEN
-----------------------------------------
`post_init_hook` körs bara vid `--init`. En redan installerad databas
uppgraderas med `--update`, som inte kör init-hooks. Utan denna
migration skulle kolumnen förbli text på alla existerande installationer.

VAD SOM INTE PÅVERKAS
---------------------
- Inga rader raderas. Endast en kolumntyp ändras (DDL).
- Körningen är idempotent: en kolumn som redan är vector lämnas orörd.
- Saknas `vector`-extensionen hoppas allt över med en varning — Odoo
  fortsätter fungera, den semantiska signalen är bara avstängd (och
  `search_for_user` hoppar nu över den i stället för att krascha).
"""

import logging

_logger = logging.getLogger(__name__)

# (tabell, kolumn) — samma två tabeller som hooks.py rör.
_TARGETS = (
    ('ai_personal_memory', 'embedding'),
    ('ai_company_memory', 'embedding'),
)

# Samma dimension som ai_okf_concept.embedding = vector(1024).
EMBEDDING_DIM = 1024


def _table_exists(cr, table):
    cr.execute(
        "SELECT 1 FROM information_schema.tables "
        "WHERE table_name = %s AND table_schema = 'public'",
        (table,))
    return cr.fetchone() is not None


def _column_type(cr, table, column):
    cr.execute(
        "SELECT udt_name FROM information_schema.columns "
        "WHERE table_name = %s AND column_name = %s",
        (table, column))
    row = cr.fetchone()
    return row[0] if row else None


def migrate(cr, version):
    _logger.info(
        'Running migration 18.0.1.244: embedding text → vector(%d)',
        EMBEDDING_DIM)

    # Extensionen måste finnas innan vi kan casta till vector.
    cr.execute("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
    if not cr.fetchone():
        _logger.warning(
            '18.0.1.244: pgvector-extensionen saknas — hoppar över '
            'konverteringen. Den semantiska sökvägen är avstängd tills '
            'extensionen installerats (CREATE EXTENSION vector).')
        return

    converted = 0
    for table, column in _TARGETS:
        if not _table_exists(cr, table):
            _logger.info(
                '18.0.1.244: %s finns inte — hoppar över', table)
            continue

        current = _column_type(cr, table, column)
        if current is None:
            _logger.info(
                '18.0.1.244: %s.%s finns inte — hoppar över',
                table, column)
            continue

        if current == 'vector':
            _logger.info(
                '18.0.1.244: %s.%s är redan vector — ingen åtgärd',
                table, column)
            continue

        # Varför CASE och inte ett rakt `USING embedding::vector(1024)`:
        # en enda otolkbar sträng hade fällt hela ALTER:en och lämnat
        # kolumnen som text — exakt det läge migrationen ska bota.
        # NULL är ett ärligt svar för "ingen vektor"; den semantiska
        # signalen hoppar över raden (embedding IS NOT NULL).
        cr.execute(
            "ALTER TABLE %s ALTER COLUMN %s TYPE vector(%d) "
            "USING CASE WHEN %s IS NULL OR %s = '' THEN NULL "
            "ELSE %s::vector(%d) END"
            % (table, column, EMBEDDING_DIM,
               column, column, column, EMBEDDING_DIM))
        _logger.info(
            '18.0.1.244: %s.%s konverterad %s → vector(%d)',
            table, column, current, EMBEDDING_DIM)
        converted += 1

    if converted:
        _logger.info(
            '18.0.1.244: %d kolumn(er) konverterade — semantisk sökning '
            'på legacy-minnena fungerar nu', converted)
    else:
        _logger.info('18.0.1.244: inget att konvertera')
