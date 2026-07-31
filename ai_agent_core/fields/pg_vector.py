# -*- coding: utf-8 -*-
"""Minimal PgVector-fält för OKF — ingen import från ai_agent_pgvector.

Skapar en riktig pgvector-kolumn (vector(n)) med ivfflat/hnsw-index.
Kräver att postgres-extensionen 'vector' är aktiverad i databasen
(hanteras i migration/post_init via CREATE EXTENSION IF NOT EXISTS).
"""

import logging

from odoo import fields, tools

_logger = logging.getLogger(__name__)

try:
    from pgvector import Vector
except ImportError:
    Vector = None
    _logger.warning('pgvector python-paket saknas — OKF-embedding fungerar inte')


class PgVector(fields.Field):
    """pgvector column type for OKF concepts (dimension 1024)."""

    type = 'pgvector'
    column_type = ('vector', 'vector')

    _slots = {
        'dimension': None,
    }

    def __init__(self, string=None, dimension=None, **kwargs):
        super().__init__(string=string, **kwargs)
        self.dimension = dimension

    def convert_to_column(self, value, record, values=None, validate=True):
        """Konvertera Python-värde (sträng-vektorliteral) till databas-format."""
        if value is None:
            return None
        if Vector is None:
            return None
        # value är en pgvector-literal-sträng "[0.1,0.2,...]" eller list
        if isinstance(value, str):
            value = value.strip()
            if value.startswith('[') and value.endswith(']'):
                value = value[1:-1].split(',')
        return Vector._to_db(value, self.dimension)

    def convert_to_cache(self, value, record, validate=True):
        """Konvertera DB-värde (vector-typ) till cache (list)."""
        if value is None:
            return None
        if Vector is None:
            return value
        if isinstance(value, str):
            return Vector._from_db(value)
        return value

    def create_column(self, cr, table, column, **kwargs):
        """Skapa vector-kolumnen. Kräver att 'vector'-extensionen finns."""
        dim_spec = '(%d)' % self.dimension if self.dimension else ''
        cr.execute(
            "ALTER TABLE %s ADD COLUMN IF NOT EXISTS %s vector%s"
            % (table, column, dim_spec))
        tools.set_column_type(cr, table, column, 'vector%s' % dim_spec)

    def create_index(self, cr, table, column, index_name, dimensions,
                     force=False):
        """Skapa ivfflat-index om det inte finns. Idempotent."""
        if force:
            cr.execute('DROP INDEX IF EXISTS %s' % index_name)
        else:
            cr.execute(
                'SELECT 1 FROM pg_indexes WHERE indexname = %s',
                (index_name,))
            if cr.fetchone():
                return
        try:
            cr.execute("""
                CREATE INDEX %s ON %s
                USING ivfflat (%s vector_cosine_ops) WITH (lists = 100)
            """ % (index_name, table, column))
            _logger.info('Created ivfflat index %s', index_name)
        except Exception as e:
            _logger.warning('Could not create ivfflat index %s: %s',
                            index_name, e)
