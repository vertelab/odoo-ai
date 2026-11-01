# -*- coding: utf-8 -*-
"""Migrate to 18.0.2.0.23: minneskolumnerna får sin dimension.

Bakgrund: `ai_personal_memory.embedding` och `ai_company_memory.embedding`
är dimensionslös `vector` i drift (mätt i `dina`, 2026-10-30), inte
`vector(1024)`. Följden är att ivfflat-indexet aldrig kunnat skapas —
`CREATE INDEX ... USING ivfflat` faller på "column does not have
dimensions" — så sökningen går på sekventiell scan.

Rotorsaken var cirkulär logik i `post_init_hook_personal_memory`: den läste
kolumntypen en gång och använde samma gamla värde för att styra både
konverteringen och index-skapandet. Efter en lyckad konvertering beskrev
`row` inte längre verkligheten, så index-blocket kördes aldrig i samma
anrop — och nästa uppgradering hoppade över ALTER eftersom kolumnen då såg
ut att vara `vector`. Båda grenarna var döda.

Migrationen finns för att `post_init_hook` bara körs vid INSTALLATION. Utan
den skulle ett redan installerat system aldrig repareras. Den är en tunn
anropare — all logik bor i `ensure_vector_column`, som läser kolumntypen
EFTER varje steg i stället för att spara ett "före"-värde.

Fel PROPAGERAS medvetet: ett saknat ivfflat-index syns aldrig i ett svar,
bara som sekventiell scan i last. En tyst varning är en tyst degradering.
"""

import logging

_logger = logging.getLogger(__name__)

# Tabeller som ska ha vector(1024) + ivfflat-index efter uppgraderingen.
# `ai_memory` hanteras av ai-memory-pgvector (memory_ensure_search_infrastructure)
# och `ai_okf_concept` av okf_ensure_search_infrastructure — båda är redan
# korrekta i drift och rörs inte här.
VECTOR_TABLES = ('ai_personal_memory', 'ai_company_memory')


def _migrate_agent_id(cr):
    """Flytta `ai_memory.agent_id` → `ai.agent.memory` och droppa kolumnen.

    Kolumnen är kvar sedan `rag_memory_ids` togs bort (ai-memory-okf-format)
    men läses av ingen kod. Att droppa utan att flytta vore att tyst tappa
    en koppling som en annan installation kan ha satt.
    """
    cr.execute("""
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'ai_memory' AND column_name = 'agent_id'
          AND table_schema = 'public'
    """)
    if not cr.fetchone():
        _logger.info('ai_memory.agent_id finns inte — inget att göra')
        return

    # Finns kopplingstabellen? Annars kan inget flyttas.
    cr.execute("""
        SELECT 1 FROM information_schema.tables
        WHERE table_name = 'ai_agent_memory' AND table_schema = 'public'
    """)
    if not cr.fetchone():
        _logger.warning(
            'ai_memory.agent_id finns men ai_agent_memory saknas — '
            'flytten hoppas över och kolumnen lämnas orörd')
        return

    # Flytta de rader som har agent_id satt och saknar en koppling.
    # ai.agent.memory har (agent_id, memory_id, sequence) — ingen 'role'.
    cr.execute("""
        INSERT INTO ai_agent_memory (agent_id, memory_id, sequence)
        SELECT m.agent_id, m.id, 10
        FROM ai_memory m
        WHERE m.agent_id IS NOT NULL
          AND NOT EXISTS (
              SELECT 1 FROM ai_agent_memory l
              WHERE l.agent_id = m.agent_id AND l.memory_id = m.id
          )
    """)
    moved = cr.rowcount
    _logger.info('ai_memory.agent_id: flyttade %d koppling(ar) till '
                 'ai_agent_memory', moved)

    if moved == 0:
        cr.execute('SELECT count(*) FROM ai_memory WHERE agent_id IS NOT NULL')
        remaining = cr.fetchone()[0]
        if remaining:
            _logger.info(
                'ai_memory.agent_id: %d rad(er) hade redan en koppling — '
                'inget flyttades', remaining)

    # Droppa kolumnen. Kontrollen ovan garanterar att den finns; en blind
    # drop skulle fälla uppgraderingen om den redan var borta.
    cr.execute('ALTER TABLE ai_memory DROP COLUMN agent_id')
    _logger.info('ai_memory.agent_id: kolumnen droppad')


def migrate(cr, version):
    from odoo.addons.ai_agent_core.hooks import ensure_vector_column

    _logger.info('Running migration 18.0.2.0.23: vektor-kolumner + index')

    for table in VECTOR_TABLES:
        result = ensure_vector_column(cr, table)
        if result['skipped']:
            _logger.info(
                '%s: hoppade över (%s)', table, result['skipped'])
            continue
        _logger.info(
            '%s: kolumn %s, index %s',
            table,
            'konverterad till vector(1024)' if result['converted']
            else 'redan vector(1024)',
            'skapat' if result['index_created'] else 'fanns redan')

    # Verifiera resultatet — migrationen ska inte kunna rapportera framgång
    # utan att indexet faktiskt finns (det var precis vad den gamla hooken
    # gjorde: loggade "klart" medan indexet saknades).
    for table in VECTOR_TABLES:
        cr.execute("""
            SELECT 1 FROM information_schema.tables
            WHERE table_name = %s AND table_schema = 'public'
        """, (table,))
        if not cr.fetchone():
            continue
        cr.execute("""
            SELECT format_type(a.atttypid, a.atttypmod)
            FROM pg_attribute a
            JOIN pg_class c ON a.attrelid = c.oid
            JOIN pg_namespace n ON c.relnamespace = n.oid
            WHERE c.relname = %s AND a.attname = 'embedding'
              AND n.nspname = 'public' AND a.attnum > 0 AND NOT a.attisdropped
        """, (table,))
        row = cr.fetchone()
        if not row or row[0] != 'vector(1024)':
            raise Exception(
                'Migration 18.0.2.0.23: %s.embedding är %r efter '
                'migreringen, förväntade vector(1024)'
                % (table, row[0] if row else None))

        cr.execute("""
            SELECT 1 FROM pg_indexes
            WHERE tablename = %s AND indexname = %s
        """, (table, 'idx_%s_embedding' % table))
        if not cr.fetchone():
            raise Exception(
                'Migration 18.0.2.0.23: ivfflat-index saknas på %s efter '
                'migreringen' % table)

    _logger.info('Vektor-kolumner + ivfflat-index verifierade')

    _migrate_agent_id(cr)
