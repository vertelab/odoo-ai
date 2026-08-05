# -*- coding: utf-8 -*-
"""Migrate to 1.11: OKF-lager — artifact types, koncept-vektorer, index.

1. Sätter default artifact_type 'learning' på ai.memory-poster som saknar typ
   (uppgraderingsvägen motsvarar post_init_hook vid installation).
2. Skapar pgvector-extensionen 'vector' (om tillgänglig).
3. Skapar search_vector tsvector GENERATED ALWAYS (swedish) + GIN-index
   på ai_okf_concept.
4. Konverterar embedding-kolumnen till riktig vector(1024) + ivfflat-index.
5. B-tree-index för (scope, concept_key, version DESC).
"""

import logging

_logger = logging.getLogger(__name__)


def _okf_default_artifact_types(cr):
    from odoo.api import Environment, SUPERUSER_ID
    env = Environment(cr, SUPERUSER_ID, {})
    try:
        learning = env.ref('ai_agent_core.artifact_type_learning',
                           raise_if_not_found=False)
        if not learning:
            _logger.warning('OKF: learning artifact type saknas — hoppar')
            return
        memories = env['ai.memory'].search([('artifact_type_id', '=', False)])
        if memories:
            memories.write({'artifact_type_id': learning.id})
            _logger.info('OKF: satte default artifact_type learning på %s poster',
                         len(memories))
        else:
            _logger.info('OKF: inga ai.memory-poster utan typ')
    except Exception as e:
        _logger.warning('OKF migration default artifact types failed: %s', e)


def _okf_vector_infrastructure(cr):
    """pgvector + tsvector-infrastruktur för ai.okf.concept. Idempotent."""
    # 1. pgvector-extension
    try:
        cr.execute("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
        if not cr.fetchone():
            pass  # vector extension managed by SaltStack/DBA
            _logger.info('Created pgvector extension')
    except Exception as e:
        _logger.warning('pgvector extension unavailable (non-fatal): %s', e)

    # 2. tsvector GENERATED COLUMN (summary + title)
    try:
        cr.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'ai_okf_concept'
              AND column_name = 'search_vector'
        """)
        if not cr.fetchone():
            cr.execute("""
                ALTER TABLE ai_okf_concept
                ADD COLUMN search_vector tsvector
                GENERATED ALWAYS AS (
                    to_tsvector('swedish',
                                coalesce(summary, '') || ' ' ||
                                coalesce(title, ''))
                ) STORED
            """)
            _logger.info('Created search_vector on ai_okf_concept')
    except Exception as e:
        _logger.warning('search_vector creation failed (non-fatal): %s', e)

    # 3. GIN-index för fulltext
    try:
        cr.execute("""
            SELECT 1 FROM pg_indexes
            WHERE tablename = 'ai_okf_concept'
              AND indexname = 'idx_ai_okf_concept_fts'
        """)
        if not cr.fetchone():
            cr.execute("""
                CREATE INDEX idx_ai_okf_concept_fts
                ON ai_okf_concept USING GIN(search_vector)
            """)
            _logger.info('Created GIN index on search_vector')
    except Exception as e:
        _logger.warning('GIN index creation failed (non-fatal): %s', e)

    # 4. embedding-kolumn → vector(1024)
    try:
        cr.execute("""
            SELECT data_type FROM information_schema.columns
            WHERE table_name = 'ai_okf_concept'
              AND column_name = 'embedding'
        """)
        row = cr.fetchone()
        if row and row[0] != 'USER-DEFINED':
            cr.execute("""
                ALTER TABLE ai_okf_concept
                ALTER COLUMN embedding TYPE vector(1024)
                USING embedding::vector(1024)
            """)
            _logger.info('Converted embedding column to vector(1024)')
    except Exception as e:
        _logger.warning('embedding column conversion failed (non-fatal): %s', e)

    # 5. ivfflat-index
    try:
        cr.execute("""
            SELECT 1 FROM pg_indexes
            WHERE tablename = 'ai_okf_concept'
              AND indexname = 'idx_ai_okf_concept_embedding'
        """)
        if not cr.fetchone():
            cr.execute("""
                CREATE INDEX idx_ai_okf_concept_embedding
                ON ai_okf_concept
                USING ivfflat (embedding vector_cosine_ops)
                WITH (lists = 100)
            """)
            _logger.info('Created ivfflat index on embedding')
    except Exception as e:
        _logger.warning('ivfflat index creation failed (non-fatal): %s', e)

    # 6. B-tree-index för scope + concept_key + version
    try:
        cr.execute("""
            SELECT 1 FROM pg_indexes
            WHERE tablename = 'ai_okf_concept'
              AND indexname = 'idx_ai_okf_concept_scope_key'
        """)
        if not cr.fetchone():
            cr.execute("""
                CREATE INDEX idx_ai_okf_concept_scope_key
                ON ai_okf_concept (scope, concept_key, version DESC)
            """)
            _logger.info('Created B-tree index on (scope, concept_key)')
    except Exception as e:
        _logger.warning('B-tree index creation failed (non-fatal): %s', e)


def migrate(cr, version):
    _logger.info("Running migration 1.9: OKF-lager")
    _okf_default_artifact_types(cr)
    _okf_vector_infrastructure(cr)
