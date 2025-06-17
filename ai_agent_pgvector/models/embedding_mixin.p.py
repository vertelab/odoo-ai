import logging

from pgvector import Vector

from odoo import api, fields, models

from odoo.addons.ai_agent_pgvector.fields.fields import PgVector

_logger = logging.getLogger(__name__)


class EmbeddingMixin(models.AbstractModel):
    """
    Mixin for models that use vector embeddings.
    Provides common functionality for vector search operations.
    """

    _name = "llm.embedding.mixin"
    _description = "Vector Embedding Mixin"

    # embedding = PgVector(
    #     string="Embedding",
    #     help="Vector embedding for similarity search",
    # )

    # embedding_model_id = fields.Many2one(
    #     "llm.model",
    #     string="Embedding Model",
    #     domain="[('model_use', '=', 'embedding')]",
    #     help="The model used to create embeddings",
    # )

    @api.model
    def search_similar(
        self, query_vector, domain=None, limit=10, min_similarity=0.0, operator="<=>"
    ):
        """
        Search for similar records using vector similarity.
        This is implemented as a class method (api.model) to allow searching
        across all records, not just a specific recordset.

        Args:
            query_vector: The query embedding vector (list, numpy array)
            domain: Additional domain filter (optional)
            limit: Maximum number of results to return
            min_similarity: Minimum similarity threshold (0-1)
            operator: Similarity operator to use:
                '<->' for L2 distance (Euclidean)
                '<#>' for inner product
                '<=>' for cosine distance

        Returns:
            A tuple containing:
            - Recordset of matching records, ordered by similarity
            - List of similarity scores for each record
        """
        # Format the query vector using pgvector's Vector class
        vector_str = Vector._to_db(query_vector)

        # Determine the table and embedding column
        model_table = self._table
        embedding_column = "embedding"

        # Build the domain clause
        domain_clause = ""
        params = [min_similarity, limit]

        if domain:
            # Correctly calculate the WHERE clause using the model itself    
            # #if VERSION <= "17.0"
            query_obj = self.env[self._name].sudo()._where_calc(domain)
            tables, where_clause, where_params = query_obj.get_sql()
            # #elif VERSION >= "18.0"
            query_obj = self.env[self._name].sudo()._where_calc(domain).select()
            _logger.warning(f"{query_obj=}")
            where_clause = query_obj.code.split(" WHERE ")[1]
            where_params = query_obj.params
            # #endif

            if where_clause:
                domain_clause = f"AND {where_clause}"
                params = [min_similarity] + where_params + [limit]

        _logger.error(f"{domain=}")
        _logger.error(f"{domain_clause=}")

        # Execute the search query with selected operator
        # Modify the query to use the vector only once (storing it in a CTE)
        query = f"""
            WITH query_vector AS (
                SELECT '{vector_str}'::vector AS vec
            )
            SELECT id, 1 - ({embedding_column} {operator} query_vector.vec) as similarity
            FROM {model_table}, query_vector
            WHERE {embedding_column} IS NOT NULL
            AND (1 - ({embedding_column} {operator} query_vector.vec)) >= %s
            {domain_clause}
            ORDER BY similarity DESC
            LIMIT %s
        """
        
        # _logger.error(f"{query=}")

        self.env.cr.execute(query, params)
        results = self.env.cr.fetchall()

        if not results:
            return self.browse([]), []

        # Extract record IDs and similarity scores
        record_ids = [row[0] for row in results]
        similarities = [row[1] for row in results]

        # Return the matching records and their similarity scores
        return self.browse(record_ids), similarities
