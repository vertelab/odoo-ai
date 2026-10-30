# -*- coding: utf-8 -*-
"""18.0.2.0.22 — ai.memory bär OKF-formatet (ai-memory-okf-format T4).

Tre saker, i den ordning de måste ske:

1. `content` → `okf_body`. `content` blev ett compute/inverse-alias i
   denna ändring, så kolumnen är död — men datan i den är det enda som
   finns. Flytta först, droppa sedan. Ordningen är inte valfri.

2. `role` på ai.agent.memory. Nya rader får default; befintliga måste
   fyllas explicit, annars står de som NULL och `role='knowledge'`-
   filtret i insamlingen tappar dem tyst.

3. Verifiera att ingen OKF-flagga uppstod på ai_memory. Kolumnerna
   `okf_dirty`/`okf_indexed_at` fanns på en äldre version av modellen och
   specen förbjuder dem. Om de ligger kvar från en tidigare uppgradering
   droppas de — men det loggas, eftersom en tyst drop är en tyst
   beteendeändring.
"""

import logging

_logger = logging.getLogger(__name__)


def _column_exists(cr, table, column):
    cr.execute("""
        SELECT 1 FROM information_schema.columns
        WHERE table_name = %s AND column_name = %s
    """, (table, column))
    return bool(cr.fetchone())


def _table_exists(cr, table):
    cr.execute("""
        SELECT 1 FROM information_schema.tables
        WHERE table_name = %s AND table_schema = 'public'
    """, (table,))
    return bool(cr.fetchone())


def migrate(cr, version):
    if not version:
        return

    # ── 1. content → okf_body ────────────────────────────────────────
    if _table_exists(cr, 'ai_memory'):
        if _column_exists(cr, 'ai_memory', 'content'):
            cr.execute("""
                UPDATE ai_memory
                SET okf_body = content
                WHERE okf_body IS NULL AND content IS NOT NULL
            """)
            moved = cr.rowcount
            if moved:
                _logger.info(
                    '18.0.2.0.22: flyttade %d rader content -> okf_body',
                    moved)

            # Räkna vad som INTE kunde flyttas — de ska synas, inte tystas.
            cr.execute("""
                SELECT count(*) FROM ai_memory
                WHERE content IS NULL AND okf_body IS NULL
            """)
            empty = cr.fetchone()[0]
            if empty:
                _logger.warning(
                    '18.0.2.0.22: %d ai.memory-rader har varken content '
                    'eller okf_body — de är tomma artefakter. '
                    'ai_memory_reembed-cronen rapporterar dem vid namn.',
                    empty)

            # Kolumnen är död (fältet är compute/inverse nu). Men
            # `search_vector` är en GENERERAD kolumn över `content` — den
            # måste bort FÖRST, annars faller DROP på
            # "cannot drop column content ... because other objects depend
            # on it" (mätt 2026-10-30). Ordningen: droppa index, droppa
            # search_vector, droppa content, återskapa search_vector över
            # okf_body.
            cr.execute("DROP INDEX IF EXISTS idx_ai_memory_fts")
            cr.execute("ALTER TABLE ai_memory DROP COLUMN IF EXISTS search_vector")
            cr.execute("ALTER TABLE ai_memory DROP COLUMN content")
            _logger.info(
                '18.0.2.0.22: drog ai_memory.content (och search_vector, '
                'som var genererad över den)')

            # Återskapa search_vector över okf_body — samma definition som
            # memory_ensure_search_infrastructure använder när okf_body
            # finns.
            cr.execute("""
                ALTER TABLE ai_memory
                ADD COLUMN search_vector tsvector
                GENERATED ALWAYS AS (
                    to_tsvector('swedish',
                                coalesce(okf_body, '') || ' ' ||
                                coalesce(name, ''))
                ) STORED
            """)
            cr.execute("""
                CREATE INDEX idx_ai_memory_fts
                ON ai_memory USING GIN(search_vector)
            """)
            _logger.info(
                '18.0.2.0.22: återskapade search_vector över okf_body')

    # ── 2. role på ai.agent.memory ───────────────────────────────────
    if _table_exists(cr, 'ai_agent_memory'):
        if _column_exists(cr, 'ai_agent_memory', 'role'):
            cr.execute("""
                UPDATE ai_agent_memory SET role = 'knowledge'
                WHERE role IS NULL
            """)
            if cr.rowcount:
                _logger.info(
                    '18.0.2.0.22: satte role=knowledge på %d kopplingar',
                    cr.rowcount)

    # ── 3. Inga OKF-flaggor på ai_memory ─────────────────────────────
    if _table_exists(cr, 'ai_memory'):
        for col in ('okf_dirty', 'okf_indexed_at', 'artifact_type_id'):
            if _column_exists(cr, 'ai_memory', col):
                cr.execute('ALTER TABLE ai_memory DROP COLUMN %s' % col)
                _logger.warning(
                    '18.0.2.0.22: drog %s från ai_memory — kolumnen fanns '
                    'kvar från en äldre version och memory-specen förbjuder '
                    'den (artefakten är material, inte inlärning)', col)
