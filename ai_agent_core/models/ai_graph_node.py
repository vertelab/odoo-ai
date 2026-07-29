# -*- coding: utf-8 -*-
"""
Graph Node Definition — declarative model-to-AGE-graph mapping.

Bridge modules register graph.node.definition records via XML data files.
Each definition maps an Odoo model to an AGE label, defining which fields
become node properties and which relations become edges.

Usage (XML in bridge module):
    <record id="graph_node_knowledge_article" model="graph.node.definition">
        <field name="model_id" ref="knowledge.model_knowledge_article"/>
        <field name="graph_label">KnowledgeArticle</field>
        <field name="name_field">name</field>
        <field name="node_properties">{"title": "name", "url": "website_url"}</field>
        <field name="edge_definitions">[{
            "type": "BELONGS_TO_COMPANY",
            "target_label": "Company",
            "target_id_field": "company_id"
        }]</field>
    </record>
"""

import json
import logging
from datetime import datetime

from odoo import models, fields, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

GRAPH_LABEL_CACHE = {}  # model_name → graph_label


class GraphNodeDefinition(models.Model):
    """Defines how an Odoo model maps to an AGE graph node."""
    _name = 'graph.node.definition'
    _description = 'Graph Node Definition'
    _order = 'model_id desc'

    model_id = fields.Many2one(
        'ir.model', string='Odoo Model', required=True,
        ondelete='cascade',
        help='The Odoo model this definition maps to the AGE graph.')
    graph_label = fields.Char(
        'AGE Label', required=True,
        help='AGE node label, e.g. "KnowledgeArticle", "StrategyPlan".')
    name_field = fields.Char(
        'Name Field',
        help='Odoo field used as the node name (e.g. "name", "title").')
    node_properties = fields.Text(
        'Node Properties (JSON)',
        default='{}',
        help='Mapping: graph_property_name → odoo_field_name. '
             'Example: {"title": "name", "url": "website_url"}')
    edge_definitions = fields.Text(
        'Edge Definitions (JSON)',
        default='[]',
        help='List of edge definitions. Each edge: '
             '{"type": "BELONGS_TO", "target_label": "Company", '
             '"target_id_field": "company_id"}')
    active = fields.Boolean(
        'Active', default=True,
        help='Disable to stop syncing this model to the graph.')
    module_id = fields.Many2one(
        'ir.module.module', string='Module',
        help='The bridge module that registered this definition.')
    last_sync = fields.Datetime('Last Sync', readonly=True)
    node_count = fields.Integer('Graph Nodes', compute='_compute_node_count')
    last_error = fields.Text('Last Error', readonly=True)

    _sql_constraints = [
        ('unique_model_graph_label',
         'unique(model_id, graph_label)',
         'Each model can only have one definition per graph label!'),
    ]

    @api.depends('graph_label')
    def _compute_node_count(self):
        """Count nodes in the AGE graph for this label."""
        for rec in self:
            try:
                res = self.env['graph.executor'].cypher(
                    f"MATCH (n:{rec.graph_label}) RETURN count(n) AS cnt",
                    read_only=True,
                )
                rec.node_count = res[0]['cnt'] if res else 0
            except Exception:
                rec.node_count = 0

    def _get_properties_map(self):
        """Return node_properties as a dict."""
        self.ensure_one()
        if self.node_properties:
            try:
                return json.loads(self.node_properties)
            except (json.JSONDecodeError, TypeError):
                _logger.warning(
                    "Invalid node_properties JSON for %s", self.graph_label)
        return {}

    def _get_edge_defs(self):
        """Return edge_definitions as a list of dicts."""
        self.ensure_one()
        if self.edge_definitions:
            try:
                return json.loads(self.edge_definitions)
            except (json.JSONDecodeError, TypeError):
                _logger.warning(
                    "Invalid edge_definitions JSON for %s", self.graph_label)
        return []

    def _upsert_node(self, record):
        """Upsert a single Odoo record as an AGE node.

        Args:
            record: An Odoo recordset (single record).
        """
        self.ensure_one()
        props = {'id': record.id}
        name_val = getattr(record, self.name_field, None)
        if name_val:
            props['name'] = str(name_val)
        props['model'] = record._name

        # Add mapped properties
        prop_map = self._get_properties_map()
        for graph_field, odoo_field in prop_map.items():
            val = getattr(record, odoo_field, None)
            if val is not None:
                if isinstance(val, models.BaseModel):
                    val = val.id
                elif isinstance(val, datetime):
                    val = val.isoformat()
                props[graph_field] = str(val) if not isinstance(val, (int, float, bool)) else val

        # Build Cypher INSERT query (avoid MERGE which requires @> operator)
        # Try CREATE first — if node exists, it fails and we UPDATE instead
        set_clauses = ", ".join(
            f"n.{k} = {self._cypher_value(v)}"
            for k, v in props.items()
        )
        cypher = f"""
            MATCH (n:{self.graph_label} {{id: {record.id}}})
            SET {set_clauses}
            SET n.updated_at = timestamp()
        """
        try:
            self.env['graph.executor'].cypher_write(cypher)
            return  # Node existed, update succeeded
        except Exception:
            pass

        # Node doesn't exist — create it
        create_props = ", ".join(
            f"{k}: {self._cypher_value(v)}" for k, v in props.items()
        )
        cypher = f"""
            CREATE (n:{self.graph_label} {{{create_props}}})
        """
        try:
            self.env['graph.executor'].cypher_write(cypher)
        except Exception as e:
            _logger.error("Failed to upsert node %s #%d: %s",
                          self.graph_label, record.id, e)
            self.last_error = str(e)
            raise

    def _create_edges(self, record):
        """Create edges from an Odoo record based on edge_definitions."""
        self.ensure_one()
        edge_defs = self._get_edge_defs()
        for edge in edge_defs:
            target_field = edge.get('target_id_field')
            if not target_field:
                continue
            target_id = getattr(record, target_field, None)
            if not target_id:
                continue
            if isinstance(target_id, models.BaseModel):
                if not target_id.id:
                    continue
                target_id = target_id.id

            edge_type = edge.get('type', 'RELATES_TO')
            target_label = edge.get('target_label', 'Node')

            cypher = f"""
                MATCH (source:{self.graph_label} {{id: {record.id}}})
                MATCH (target:{target_label} {{id: {target_id}}})
                MERGE (source)-[:{edge_type}]->(target)
            """
            try:
                self.env['graph.executor'].cypher_write(cypher)
            except Exception as e:
                _logger.warning(
                    "Failed to create edge %s %s→%s #%d: %s",
                    edge_type, self.graph_label, target_label, target_id, e)

    def _sync_batch(self, model, batch_size=500):
        """Batch upsert records from an Odoo model.

        Args:
            model: Odoo model (e.g. self.env['knowledge.article']).
            batch_size: Records per transaction.
        """
        self.ensure_one()
        total = model.search_count([])
        synced = 0
        for offset in range(0, total, batch_size):
            batch = model.search([], limit=batch_size, offset=offset)
            for rec in batch:
                try:
                    self._upsert_node(rec)
                    self._create_edges(rec)
                    synced += 1
                except Exception:
                    pass  # Error already logged in _upsert_node
        self.write({
            'last_sync': fields.Datetime.now(),
            'last_error': False,
        })
        _logger.info("Synced %d/%d records for %s",
                     synced, total, self.graph_label)
        return synced

    @api.model
    def _cypher_value(self, val):
        """Format a Python value for use in Cypher SET clause."""
        if val is None:
            return "NULL"
        if isinstance(val, bool):
            return "true" if val else "false"
        if isinstance(val, (int, float)):
            return str(val)
        # String: escape single quotes and wrap
        escaped = str(val).replace("'", "''")
        return f"'{escaped}'"

    def action_sync_now(self):
        """Button action: sync this definition immediately."""
        self.ensure_one()
        model = self.env.get(self.model_id.model)
        if model is None:
            raise UserError(_("Model %s not found") % self.model_id.model)
        synced = self._sync_batch(model)
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Graph Sync Complete'),
                'message': _('%s: %d nodes synced') % (self.graph_label, synced),
                'type': 'success',
                'sticky': False,
            },
        }

    def _sync_all(self):
        """Sync all active definitions."""
        for defn in self.search([('active', '=', True)]):
            try:
                model = self.env.get(defn.model_id.model)
                if model is None:
                    _logger.warning("Model %s not found for definition %s",
                                    defn.model_id.model, defn.graph_label)
                    continue
                defn._sync_batch(model)
            except Exception as e:
                _logger.error("Sync failed for %s: %s", defn.graph_label, e)
                defn.write({'last_error': str(e)})


class GraphExecutor(models.Model):
    """Abstract model providing cypher() execution against AGE graph."""
    _name = 'graph.executor'
    _description = 'Graph Executor'
    _abstract = True

    @api.model
    def cypher(self, query, read_only=True, timeout=10):
        """Execute a Cypher query against the odoo_mind AGE graph.

        Args:
            query: Cypher query string (e.g. "MATCH (n) RETURN n LIMIT 10").
            read_only: If True, block CREATE/MERGE/DELETE/SET statements.
            timeout: Query timeout in seconds.

        Returns:
            List of dicts with query results.

        Raises:
            UserError: If query contains write operations when read_only=True.
        """
        if read_only:
            self._validate_read_only(query)

        # Escape single quotes for Cypher
        # Use dollar-quoting with a unique tag to avoid collisions
        import uuid
        tag = 'c_' + uuid.uuid4().hex[:8]
        sql = (
            "SELECT * FROM ag_catalog.cypher('odoo_mind', $"
            + tag + "$ " + query + " $" + tag + "$) "
            + "AS result (r ag_catalog.agtype)"
        )
        try:
            # Use savepoint to avoid aborting the entire transaction on error
            self.env.cr.execute("SAVEPOINT graph_executor")
            self.env.cr.execute(f"SET LOCAL statement_timeout = {timeout * 1000}")
            try:
                self.env.cr.execute(sql)
                rows = self.env.cr.fetchall()
                self.env.cr.execute("RELEASE SAVEPOINT graph_executor")
            except Exception:
                self.env.cr.execute("ROLLBACK TO SAVEPOINT graph_executor")
                self.env.cr.execute("RELEASE SAVEPOINT graph_executor")
                raise
            # Parse agtype results
            import json as json_mod
            results = []
            for row in rows:
                for cell in row:
                    try:
                        val = json_mod.loads(str(cell))
                        results.append(val)
                    except (json_mod.JSONDecodeError, TypeError):
                        results.append(str(cell))
            return results
        except Exception as e:
            _logger.error("Cypher query failed: %s\nQuery: %s", e, query[:200])
            raise UserError(_("Graph query failed: %s") % str(e))

    @api.model
    def cypher_write(self, query):
        """Execute a Cypher write query (CREATE/MERGE/DELETE/SET).

        Only callable from trusted code (cron, hooks, bridge modules).
        Not exposed to AI agents.
        """
        return self.cypher(query, read_only=False)

    @api.model
    def _validate_read_only(self, query):
        """Validate that a Cypher query is read-only.

        Blocks: CREATE, MERGE, DELETE, SET, REMOVE, LOAD, INSERT, UPDATE
        Allows: MATCH, RETURN, WHERE, OPTIONAL MATCH, WITH, ORDER BY,
                LIMIT, SKIP, UNWIND, CALL (for function calls)
        """
        import re
        # Strip comments and string literals before checking
        clean = re.sub(r'//.*', '', query)
        clean = re.sub(r"'[^']*'", '', clean)
        clean = re.sub(r'"[^"]*"', '', clean)

        write_keywords = [
            r'\bCREATE\b', r'\bMERGE\b', r'\bDELETE\b',
            r'\bSET\b', r'\bREMOVE\b', r'\bLOAD\b',
            r'\bINSERT\b', r'\bUPDATE\b',
        ]
        for kw in write_keywords:
            if re.search(kw, clean, re.IGNORECASE):
                raise UserError(_(
                    "Write operations are not allowed in graph queries. "
                    "Found: %s") % kw.strip('\\b'))

    @api.model
    def is_age_available(self):
        """Check if AGE extension is installed and graph exists."""
        try:
            self.env.cr.execute("""
                SELECT 1 FROM pg_extension WHERE extname = 'age'
            """)
            if not self.env.cr.fetchone():
                return False
            self.env.cr.execute("""
                SELECT 1 FROM ag_catalog.ag_graph WHERE name = 'odoo_mind'
            """)
            return bool(self.env.cr.fetchone())
        except Exception:
            return False
