# -*- coding: utf-8 -*-
"""Tests for graph executor and read-only validation."""

from odoo.tests.common import TransactionCase
from odoo.exceptions import UserError


class TestGraphExecutor(TransactionCase):
    """Test graph.executor cypher() method."""

    def setUp(self):
        super().setUp()
        self.executor = self.env['graph.executor']

    def test_read_only_validation_blocks_create(self):
        """CREATE should be blocked by read-only validation."""
        with self.assertRaises(UserError):
            self.executor._validate_read_only("CREATE (n:TestNode {id: 1})")

    def test_read_only_validation_blocks_merge(self):
        """MERGE should be blocked."""
        with self.assertRaises(UserError):
            self.executor._validate_read_only("MERGE (n:OdooPartner {id: 1})")

    def test_read_only_validation_blocks_delete(self):
        """DELETE should be blocked."""
        with self.assertRaises(UserError):
            self.executor._validate_read_only("MATCH (n) DELETE n")

    def test_read_only_validation_blocks_set(self):
        """SET should be blocked."""
        with self.assertRaises(UserError):
            self.executor._validate_read_only("MATCH (n) SET n.name = 'test'")

    def test_read_only_validation_allows_match(self):
        """MATCH...RETURN should pass validation."""
        try:
            self.executor._validate_read_only(
                "MATCH (p:OdooPartner {id: 42}) RETURN p.name, p.email")
        except UserError:
            self.fail("MATCH query should pass read-only validation")

    def test_read_only_validation_allows_complex(self):
        """Complex read-only queries should pass."""
        query = """
            MATCH (p:OdooPartner)-[:HAS_CONTACT]->(person)
            WHERE person.email CONTAINS 'vertel'
            RETURN p.name, person.email
            ORDER BY p.name
            LIMIT 10
        """
        try:
            self.executor._validate_read_only(query)
        except UserError:
            self.fail("Complex MATCH query should pass validation")

    def test_graph_query_tool_handler(self):
        """The graph_query tool handler should handle missing AGE gracefully."""
        from ..core.tools import _tool_graph_query
        import asyncio
        result = asyncio.run(_tool_graph_query(self.env, query="MATCH (n) RETURN n LIMIT 1"))
        # Should return either error (no AGE) or valid JSON
        self.assertIn('"error"', result)  # AGE not installed in test
