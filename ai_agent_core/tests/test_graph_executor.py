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
        """graph_query-verktyget ska hantera både fungerande och saknad AGE.

        Tidigare antog testet att AGE aldrig är installerat
        (`assertIn('"error"', result)`) — vilket dolde att grafen var tom
        och skrivvägen trasig. Nu accepteras båda utfallen, så testet inte
        låser fast ett trasigt läge.
        """
        from ..core.tools import _tool_graph_query
        import asyncio
        result = asyncio.run(
            _tool_graph_query(self.env, query="MATCH (n) RETURN n LIMIT 1"))
        # Antingen ett fel (AGE saknas) eller giltigt JSON-svar.
        self.assertTrue(
            '"error"' in result or '[' in result or '{' in result,
            'graph_query ska ge ett JSON-svar eller ett fel — inte kasta')


class TestGraphWritePath(TransactionCase):
    """odoo-mind-graph-write-path: search_path, ärlig felrapportering, cron.

    Verifierat 2026-10-05: grafen var tom, last_sync=NULL på alla
    definitioner, ingen cron. Rotorsak: ag_catalog saknades i search_path.
    """

    def setUp(self):
        super().setUp()
        self.executor = self.env['graph.executor']
        self.Def = self.env['graph.node.definition']

    def _age_available(self):
        try:
            self.executor.cypher('RETURN 1', read_only=True)
            return True
        except Exception:
            return False

    def test_search_path_makes_property_write_work(self):
        """Kärnan: property-matchad skrivning (SET) måste persisteras."""
        if not self._age_available():
            self.skipTest('AGE ej användbar i denna miljö')
        self.executor.cypher_write(
            "CREATE (n:ProbeWp {id: 900001})")
        self.executor.cypher_write(
            "MATCH (n:ProbeWp {id: 900001}) SET n.v = 42")
        res = self.executor.cypher(
            "MATCH (n:ProbeWp {id: 900001}) RETURN n.v", read_only=True)
        self.assertEqual(res, [42])
        self.executor.cypher_write(
            "MATCH (n:ProbeWp {id: 900001}) DETACH DELETE n")

    def test_merge_creates_edge_without_duplicate(self):
        if not self._age_available():
            self.skipTest('AGE ej användbar i denna miljö')
        self.executor.cypher_write("CREATE (a:ProbeA {id: 900002})")
        self.executor.cypher_write("CREATE (b:ProbeB {id: 900003})")
        for _ in range(2):
            self.executor.cypher_write(
                "MATCH (a:ProbeA {id: 900002}) "
                "MATCH (b:ProbeB {id: 900003}) "
                "MERGE (a)-[:PROBE_REL]->(b)")
        res = self.executor.cypher(
            "MATCH (a:ProbeA {id: 900002})-[:PROBE_REL]->(b) "
            "RETURN count(b)", read_only=True)
        self.assertEqual(res, [1], 'MERGE ska inte dubbla kanten')
        self.executor.cypher_write("MATCH (a:ProbeA {id: 900002}) DETACH DELETE a")
        self.executor.cypher_write("MATCH (b:ProbeB {id: 900003}) DETACH DELETE b")

    def test_delete_removes_node(self):
        if not self._age_available():
            self.skipTest('AGE ej användbar i denna miljö')
        self.executor.cypher_write("CREATE (n:ProbeDel {id: 900004})")
        self.executor.cypher_write(
            "MATCH (n:ProbeDel {id: 900004}) DETACH DELETE n")
        res = self.executor.cypher(
            "MATCH (n:ProbeDel {id: 900004}) RETURN count(n)", read_only=True)
        self.assertEqual(res, [0])

    def test_read_unchanged(self):
        if not self._age_available():
            self.skipTest('AGE ej användbar i denna miljö')
        res = self.executor.cypher('RETURN 1', read_only=True)
        self.assertEqual(res, [1])

    def test_upsert_actually_creates_node(self):
        """Femte rotorsaken: MATCH...SET mot en icke-existerande nod är en
        TYST no-op (0 rader, inget fel) — så den gamla fallbacken till
        CREATE nåddes aldrig och synken rapporterade 'synced' med 0 noder.
        MERGE skapar noden på riktigt.
        """
        if not self._age_available():
            self.skipTest('AGE ej användbar i denna miljö')
        defn = self.Def.search([('active', '=', True)], limit=1)
        if not defn:
            self.skipTest('Inga graf-definitioner')
        partner = self.env['res.partner'].search([], limit=1)
        if not partner:
            self.skipTest('Inga partners')
        # Städa eventuell rest.
        self.executor.cypher_write(
            "MATCH (n:UpsertProbe {id: %d}) DETACH DELETE n" % partner.id)
        before = self.executor.cypher(
            "MATCH (n:UpsertProbe) RETURN count(n)", read_only=True)
        # Använd _upsert_node mot en temporär definition.
        tmp = defn.copy({'graph_label': 'UpsertProbe'})
        try:
            tmp._upsert_node(partner)
            after = self.executor.cypher(
                "MATCH (n:UpsertProbe) RETURN count(n)", read_only=True)
            self.assertGreater(
                after[0], before[0],
                '_upsert_node måste skapa noden (inte tyst no-op)')
            # Idempotent: en andra körning ger ingen dubblett.
            tmp._upsert_node(partner)
            again = self.executor.cypher(
                "MATCH (n:UpsertProbe) RETURN count(n)", read_only=True)
            self.assertEqual(again[0], after[0], 'MERGE ska vara idempotent')
        finally:
            self.executor.cypher_write(
                "MATCH (n:UpsertProbe) DETACH DELETE n")
            tmp.unlink()

    def test_sync_batch_sets_last_error_on_failure(self):
        """Trasig skrivväg får inte se ut som en tom men frisk graf."""
        if not self._age_available():
            self.skipTest('AGE ej användbar i denna miljö')
        defn = self.Def.search([('active', '=', True)], limit=1)
        if not defn:
            self.skipTest('Inga graf-definitioner')
        # Tvinga ett äkta Cypher-fel: ett label med mellanslag ger
        # syntax error i varje _upsert_node → varje record misslyckas.
        original_label = defn.graph_label
        defn.write({'graph_label': 'Bad Label'})
        try:
            defn._sync_batch(self.env['res.users'])
        finally:
            defn.write({'graph_label': original_label})
        self.assertTrue(
            defn.last_error,
            'last_error ska sättas när records misslyckas')
        self.assertFalse(defn.is_healthy)

    def test_health_distinguishes_empty_from_broken(self):
        """Tom men frisk graf = healthy; last_error = inte healthy."""
        if not self._age_available():
            self.skipTest('AGE ej användbar i denna miljö')
        health = self.Def._graph_health()
        self.assertIn('healthy', health)
        self.assertIn('failing', health)
        # En definition med last_error räknas som felande.
        defn = self.Def.search([('active', '=', True)], limit=1)
        if defn:
            defn.write({'last_error': 'simulerat'})
            health = self.Def._graph_health()
            self.assertGreaterEqual(health['failing'], 1)
            self.assertFalse(health['healthy'])

    def test_ensure_graph_cron_idempotent(self):
        from odoo.addons.ai_agent_core.hooks import _ensure_graph_cron
        cron1 = _ensure_graph_cron(self.env)
        self.assertTrue(cron1, 'cronen ska skapas/finnas')
        cron2 = _ensure_graph_cron(self.env)
        self.assertEqual(cron1.id, cron2.id, 'ingen dubblett')
        # Bara en cron med namnet.
        count = self.env['ir.cron'].sudo().search_count([
            ('cron_name', '=', 'Odoo Mind Graph Sync')])
        self.assertLessEqual(count, 1)
