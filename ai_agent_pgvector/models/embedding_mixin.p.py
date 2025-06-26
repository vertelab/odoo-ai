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
            self, query_vector, domain=None, limit=10, min_similarity=0.0, embedding_column="embedding", operator="<=>"
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

        # Start with basic domain to filter out records without embeddings
        base_domain = [(embedding_column, '!=', False)]

        # Debug: Check if any records have embeddings
        all_with_embeddings = self.search([(embedding_column, '!=', False)])
        _logger.error(f"Total records with embeddings: {len(all_with_embeddings)}")

        # Debug: Check domain separately if provided
        if domain:
            domain_matches = self.search(domain)
            _logger.error(f"Records matching domain {domain}: {len(domain_matches)}")
            base_domain.extend(domain)

        # First, get all candidate records using Odoo's ORM
        # This handles all the complex JOINs properly
        candidates = self.search(base_domain)
        _logger.error(f"Final candidates after combining filters: {len(candidates)}")

        if not candidates:
            _logger.error("No candidates found - returning empty result")
            return self.browse([]), []

        # Now do the vector similarity search on the candidate IDs
        candidate_ids = tuple(candidates.ids)

        # Build the similarity query focusing only on the main table
        # since we already have the filtered candidate IDs
        query = f"""
            WITH query_vector AS (
                SELECT '{vector_str}'::vector AS vec
            )
            SELECT id, 1 - ({embedding_column} {operator} query_vector.vec) as similarity
            FROM {self._table}, query_vector
            WHERE id = ANY(%s)
            AND {embedding_column} IS NOT NULL
            AND (1 - ({embedding_column} {operator} query_vector.vec)) >= %s
            ORDER BY similarity DESC
            LIMIT %s
        """

        params = [list(candidate_ids), min_similarity, limit]

        self.env.cr.execute(query, params)
        results = self.env.cr.fetchall()

        _logger.info(f"Vector similarity results count: {len(results)}")
        if results:
            _logger.warning(f"Sample results: {results[:3]}")  # Show first 3 results

        if not results:
            return self.browse([]), []

        # Extract record IDs and similarity scores
        record_ids = [row[0] for row in results]
        similarities = [row[1] for row in results]

        # Return the matching records and their similarity scores
        return self.browse(record_ids), similarities