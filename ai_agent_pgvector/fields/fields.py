import logging

import numpy as np
from pgvector import Vector
from pgvector.psycopg2 import register_vector

from odoo import fields, tools

_logger = logging.getLogger(__name__)


class PgVector(fields.Field):
    """PgVector field for Odoo, using pgvector extension for PostgreSQL."""

    type = "pgvector"
    column_type = ("vector", "vector")

    _slots = {
        "dimension": None,  # Vector dimensions
    }

    def __init__(self, string=None, dimension=None, **kwargs):
        super().__init__(string=string, **kwargs)
        self.dimension = dimension

    def convert_to_column(self, value, record, values=None, validate=True):
        """Convert Python value to database format using pgvector.Vector."""
        if value is None:
            return None

        # Use Vector._to_db method from pgvector
        return Vector._to_db(value, self.dimension)

    def convert_to_cache(self, value, record, validate=True):
        """Convert database value to cache format."""
        if value is None:
            return None

        # Handle case where value is already a list or numpy array
        if isinstance(value, list) or isinstance(value, np.ndarray):
            return value

        # Use Vector._from_db method from pgvector for string values
        return Vector._from_db(value)

    def create_column(self, cr, table, column, **kwargs):
        """Create a vector column in the database."""
        # Register vector with this cursor
        from pgvector.psycopg2 import register_vector

        register_vector(cr)

        # Specify dimensions if provided
        dim_spec = f"({self.dimension})" if self.dimension else ""

        # Create the column with appropriate vector dimensions
        cr.execute(f"""
            ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} vector{dim_spec}
        """)

        # Update the column format to match the dimensions
        tools.set_column_type(cr, table, column, f"vector{dim_spec}")

    def create_index(
        self,
        cr,
        table,
        column,
        index_name,
        dimensions,
        model_field_name=None,
        model_id=None,
        force=False,
    ):
        """
        Create a vector index for the specified column if it doesn't already exist.

        Args:
            cr: Database cursor
            table: Table name
            column: Column name
            index_name: Name for the index
            dimensions: Vector dimensions (optional)
            model_field_name: Field name that stores model information (optional)
            model_id: Model ID to filter by (optional)
            force: If True, drop existing index and recreate it (default: False)
        """
        # Register vector with this cursor
        register_vector(cr._cnx)

        # Check if index already exists
        if force:
            # Drop existing index if force is True
            cr.execute(f"DROP INDEX IF EXISTS {index_name}")
        else:
            # Check if index exists
            cr.execute(
                """
                    SELECT 1 FROM pg_indexes
                    WHERE indexname = %s
                """,
                (index_name,),
            )

            # If index already exists, return early
            if cr.fetchone():
                _logger.info(f"Index {index_name} already exists, skipping creation")
                return

        # Determine the dimension specification
        dim_spec = f"({dimensions})" if dimensions else ""

        # Create the appropriate index with or without model filtering
        if model_field_name and model_id:
            # Create model-specific index
            cr.execute(
                f"""
                    CREATE INDEX {index_name} ON {table}
                    USING ivfflat(({column}::vector{dim_spec}) vector_cosine_ops)
                    WHERE {model_field_name} = %s AND {column} IS NOT NULL
                """,
                (model_id,),
            )
        else:
            # Create general index
            cr.execute(f"""
                    CREATE INDEX IF NOT EXISTS {index_name} ON {table}
                    USING ivfflat(({column}::vector{dim_spec}) vector_cosine_ops)
                    WHERE {column} IS NOT NULL
                """)
        _logger.info(f"Created vector index {index_name} on {table}.{column}")

    def drop_index(self, cr, index_name):
        """Drop a vector index by name."""
        cr.execute(f"DROP INDEX IF EXISTS {index_name}")
        _logger.info(f"Dropped vector index {index_name}")
