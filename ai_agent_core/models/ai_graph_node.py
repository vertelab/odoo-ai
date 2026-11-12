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
    is_healthy = fields.Boolean(
        'Healthy', compute='_compute_is_healthy',
        help='False when the last sync reported an error — separates a broken write path from a healthy but empty graph.')

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

    @api.depends('last_error')
    def _compute_is_healthy(self):
        """Frisk = ingen senaste felrapport. En tom men frisk graf är frisk."""
        for rec in self:
            rec.is_healthy = not rec.last_error

    @api.model
    def _graph_health(self):
        """Sammanfattning för övervakning (Zabbix).

        Returnerar antal aktiva definitioner, hur många som felar, och om
        grafen alls är nåbar. En tom men frisk graf rapporteras som frisk —
        bara en faktisk felrapport räknas som ohälsa.
        """
        defs = self.search([('active', '=', True)])
        failing = defs.filtered(lambda d: d.last_error)
        try:
            self.env['graph.executor'].cypher(
                'RETURN 1', read_only=True)
            reachable = True
        except Exception:
            reachable = False
        return {
            'definitions': len(defs),
            'failing': len(failing),
            'reachable': reachable,
            'healthy': reachable and not failing,
        }

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
        # MERGE är rätt primitiv för upsert: skapa om noden saknas, annars
        # matcha. Den krävde tidigare @>-operatorn som inte resolvades
        # (search_path saknade ag_catalog) — det är nu åtgärdat i
        # graph.executor.cypher().
        #
        # FÖRE: "MATCH ... SET, annars CREATE". Den logiken var tyst trasig:
        # MATCH mot en icke-existerande nod matchar 0 rader och SET lyckas
        # utan fel — så `return` kördes och CREATE nåddes aldrig. Synken
        # rapporterade "63/63 synced" med 0 noder i grafen.
        merge_props = ", ".join(
            f"{k}: {self._cypher_value(v)}" for k, v in props.items()
        )
        set_clauses = ", ".join(
            f"n.{k} = {self._cypher_value(v)}"
            for k, v in props.items()
        )
        cypher = f"""
            MERGE (n:{self.graph_label} {{id: {record.id}}})
            SET {set_clauses}
            SET n.updated_at = timestamp()
        """
        try:
            self.env['graph.executor'].cypher_write(cypher)
        except Exception as e:
            _logger.error("Failed to upsert node %s #%d: %s",
                          self.graph_label, record.id, e)
            self.last_error = str(e)
            raise

    def _create_edges(self, record):
        """Create edges from an Odoo record based on edge_definitions.

        `target_id_via` (valfritt): ett fältnamn som löses upp på målvärdet
        innan nod-id:t beräknas. Används när `target_id_field` pekar på en
        modell vars id-rum skiljer sig från nodens — t.ex. `res.users`,
        som är samma objekt som `res.partner` (`_inherits`), men har ett
        annat `id`. Utan upplösningen matchar `{id: user_id}` fel nod
        (mätt: `res_users.id = res_partner.id` i 0 av 7 fall i luke18).

        Upplösningen är explicit per edge — ingen automatisk härledning av
        delegationskedjor.
        """
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
                via = edge.get('target_id_via')
                if via:
                    target_id = getattr(target_id, via, None)
                    if not target_id:
                        continue
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

    @api.model
    def _undefined_edge_labels(self):
        """Returnera de `target_label` i denna definitions edges som saknar
        en registrerad `graph.node.definition`.

        En kant mot ett odefinierat label skapas aldrig (`MATCH` mot en
        icke-existerande nod matchar 0 rader) — det är samma tysta nolla
        som `_upsert_node` hade. Här görs den synlig i stället.
        """
        self.ensure_one()
        labels = {
            e.get('target_label')
            for e in self._get_edge_defs()
            if e.get('target_label')
        }
        # Nodens eget label räknas inte som mål.
        labels.discard(self.graph_label)
        if not labels:
            return []
        defined = set(self.search([
            ('graph_label', 'in', list(labels)),
        ]).mapped('graph_label'))
        return sorted(labels - defined)

    def _sync_batch(self, model, batch_size=500):
        """Batch upsert records from an Odoo model.

        Args:
            model: Odoo model (e.g. self.env['knowledge.article']).
            batch_size: Records per transaction.

        Ärlig felrapportering: misslyckade records räknas och sätts i
        `last_error`. En trasig skrivväg får inte se ut som en tom men
        frisk graf (samma "tysta nolla" som okf-recall-path utrotade).
        """
        self.ensure_one()
        total = model.search_count([])
        synced = 0
        failed = 0
        first_error = None
        for offset in range(0, total, batch_size):
            batch = model.search([], limit=batch_size, offset=offset)
            for rec in batch:
                try:
                    self._upsert_node(rec)
                    self._create_edges(rec)
                    synced += 1
                except Exception as e:
                    # ROLLBACK PER RECORD, or one bad record poisons the batch.
                    #
                    # A failed cypher() leaves the transaction aborted; the
                    # next record's query then fails with "current
                    # transaction is aborted" rather than its own error, and
                    # so does every record after it. The batch reports N
                    # failures where the true number is 1, and first_error
                    # describes the consequence instead of the cause.
                    #
                    # Verified on dms 2026-11-10: after the first cypher
                    # failure every remaining record logged "current
                    # transaction is aborted", and _sync_all's handler then
                    # crashed reading defn.graph_label.
                    #
                    # SPARPUNKT, INTE cr.rollback() (fix 2026-11-11):
                    # `cr.rollback()` rullar tillbaka HELA transaktionen —
                    # inklusive det testramverket äger. Odoo:s
                    # TransactionCase fångar det med
                    # "forbidden to call rollback() in a test" och fäller
                    # hela sviten. Mätt i ledningssystem_copy: 42 gröna
                    # tester blev RC=255 så fort en synk misslyckades.
                    #
                    # En sparpunkt rullar bara tillbaka det som hände sedan
                    # den sattes — exakt samma avsikt, utan att röra
                    # anroparens transaktion. Mönstret används redan i
                    # ai_coworker.py.
                    failed += 1
                    if first_error is None:
                        first_error = str(e)
                    _logger.warning(
                        "Graph sync failed for %s #%d: %s",
                        self.graph_label, rec.id, e)

        # Kants-mål: en edge mot ett odefinierat label skapas aldrig.
        # Gör det till ett synligt fel i stället för en tyst nolla.
        undefined = self._undefined_edge_labels()
        if undefined:
            msg = ('%s: edge target without a node definition: %s'
                   % (self.graph_label, ', '.join(undefined)))
            _logger.warning(msg)
            failed += 1
            if first_error is None:
                first_error = msg

        self.write({
            'last_sync': fields.Datetime.now(),
            'last_error': (
                '%d/%d misslyckades: %s' % (failed, total, first_error)
                if failed else False),
        })
        if failed:
            _logger.warning(
                "Synced %d/%d records for %s (%d failed)",
                synced, total, self.graph_label, failed)
        else:
            _logger.info("Synced %d/%d records for %s",
                         synced, total, self.graph_label)
        return synced

    @api.model
    def _cypher_value(self, val):
        """Format a Python value for use in Cypher SET clause.

        AGE:s Cypher-parser följer INTE SQL-standardens ''-escaping inuti
        Cypher-kroppen — `'Carl Falk''s'` ger `syntax error at or near
        "'Carl Falk'"`. Backslash-escape (`\'`) fungerar och läses tillbaka
        korrekt. Verifierat mot odoo_mind 2026-10-08.

        Före fixen: en partner vars namn innehöll ett citattecken (t.ex.
        `'Carl Falk'`, importerat med citattecknen i datan) kraschade
        _upsert_node → 5/3626 partners misslyckades. Felet var osynligt
        innan last_error fanns (rapporterades som "Synced 3626/3626").
        """
        if val is None:
            return "NULL"
        if isinstance(val, bool):
            return "true" if val else "false"
        if isinstance(val, (int, float)):
            return str(val)
        # String: escape backslash först, sedan citattecken (AGE-stil).
        escaped = str(val).replace("\\", "\\\\").replace("'", "\\'")
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
                # ROLLBACK BEFORE TOUCHING THE ORM AGAIN.
                #
                # The failing statement (a cypher() call) leaves the
                # transaction aborted. Reading `defn.graph_label` or
                # writing `last_error` on an aborted transaction raises
                # InFailedSqlTransaction — so the error handler itself
                # crashed, and the log line meant to explain the failure
                # became a second, unrelated traceback.
                #
                # Verified on dms 2026-11-10:
                #   ai_graph_node.py:304  -> cypher failed
                #   ai_graph_node.py:385  -> logging the failure crashed
                #   cron_task.py:699      -> recording it crashed too
                # Three tracebacks for one root cause, none naming it.
                # Ingen cr.rollback() — se kommentaren i _sync_batch.
                # Transaktionen är redan återställd av sparpunkten runt
                # anropet; en rollback här river anroparens transaktion.
                _logger.error("Sync failed for %s: %s", defn.graph_label, e)
                defn.write({'last_error': str(e)})


class GraphExecutor(models.Model):
    """Abstract model providing cypher() execution against AGE graph."""
    _name = 'graph.executor'
    _description = 'Graph Executor'
    _abstract = True

    @api.model
    def _load_age_extension(self):
        """Ladda AGE-biblioteket i den här sessionen (``LOAD 'age'``).

        Fällan (2026-11-09): ``ag_catalog.cypher()`` svarar
        ``unhandled cypher(cstring) function call`` om AGE:s bibliotek inte
        är laddat i sessionen — även när extensionen finns, grafen finns och
        ``search_path`` innehåller ``ag_catalog``. Verifierat manuellt:

            SET search_path = ag_catalog, public;
            SELECT * FROM ag_catalog.cypher('odoo_mind', $$ RETURN 1 $$);
            -- ERROR:  unhandled cypher(cstring) function call

            LOAD 'age';                       -- ← den här raden krävs
            SET search_path = ag_catalog, public;
            SELECT * FROM ag_catalog.cypher('odoo_mind', $$ RETURN 1 $$);
            -- 1

        ``LOAD`` är sessionslokalt och billigt (biblioteket är redan laddat
        andra gången). Vi sväljer fel: saknas extensionen ska anroparen få
        sitt eget, tydligare fel från ``cypher()``.
        """
        # EGEN SPARPUNKT. Anroparen har redan en (`graph_executor`), men
        # den här metoden får inte lämna en förgiftad transaktion efter
        # sig: PostgreSQL tillåter inga kommandon alls efter ett nekat
        # LOAD, inte ens ROLLBACK TO. Utan sparpunkten här dog hela
        # installationen på en graf som bara skulle ha degraderat
        # (mätt 2026-11-10).
        try:
            self.env.cr.execute("SAVEPOINT age_load")
            try:
                self.env.cr.execute("LOAD 'age'")
                self.env.cr.execute("RELEASE SAVEPOINT age_load")
            except Exception:
                self.env.cr.execute("ROLLBACK TO SAVEPOINT age_load")
                self.env.cr.execute("RELEASE SAVEPOINT age_load")
        except Exception:
<<<<<<< Updated upstream
            # ROLLBACK IS MANDATORY, not defensive.
            #
            # A failed statement inside a transaction leaves PostgreSQL in
            # "current transaction is aborted": every later statement returns
            # that same error until someone rolls back. Without this line the
            # own caller — ``cypher()`` right below — fails with a message
            # about an aborted transaction instead of about AGE, and so does
            # every unrelated query in the same unit of work.
            #
            # Verified on dms 2026-11-10. AGE 1.6 refuses LOAD for a
            # non-superuser role ("access to library 'age' is not allowed"),
            # so this path is the NORMAL one there, not an edge case:
            #
            #   LOAD 'age'                     -> ERROR (swallowed)
            #   SET LOCAL search_path = ...    -> ERROR: transaction is aborted
            #   ... every later statement      -> same
            #   env.cr.commit()                -> "Failed to load registry"
            #
            # The whole database installation died on a missing rollback.
            #
            # The savepoint is what makes the rollback possible without
            # discarding the caller's work: with it, only this statement is
            # undone.
            # FIXED 2026-11-10: this used to be a plain rollback(), which
            # rolls back the WHOLE transaction — including the savepoint
            # `cypher()` had already created. The next statement then said
            #   UserError: Graph query failed: savepoint "graph_executor"
            #   does not exist
            # so every cypher() call failed for a second, unrelated reason.
            #
            # cypher() calls this method INSIDE `SAVEPOINT graph_executor`,
            # so that is the level to roll back to. ROLLBACK TO SAVEPOINT
            # undoes only the failed LOAD and leaves the savepoint usable.
            # With no savepoint (a direct call) the plain rollback is the
            # only option — hence the check.
            try:
                self.env.cr.execute("ROLLBACK TO SAVEPOINT graph_executor")
            except Exception:
                # Fallbacken får INTE vara en naken rollback() (fix
                # 2026-11-11): i ett test äger TransactionCase
                # transaktionen, och en rollback fäller hela sviten med
                # "forbidden to call rollback() in a test". Utan sparpunkt
                # finns inget att rulla tillbaka till — då är det ärliga
                # svaret att låta felet stå kvar och rapporteras, inte att
                # riva anroparens transaktion.
                _logger.debug(
                    'LOAD age failed outside a savepoint; leaving the '
                    'transaction as is')
=======
            # Även sparpunkten kan falla (t.ex. utanför en transaktion).
            # Best-effort kvarstår: saknas extensionen ska `cypher()`
            # rapportera det med ett tydligare fel.
            pass
>>>>>>> Stashed changes

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

        self._refuse_unlabelled_match(query)

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
            # `LOAD 'age'` MÅSTE ligga innanför savepointet.
            #
            # FYND 2026-11-10: den låg utanför, och `_load_age_extension()`
            # sväljer felet med `except Exception: pass`. Det räddar Python
            # — men inte TRANSAKTIONEN. När PostgreSQL nekar LOAD ("access
            # to library "age" is not allowed", t.ex. på en roll utan
            # superuser) är transaktionen redan förgiftad, och varje
            # efterföljande kommando svarar "current transaction is
            # aborted". Följden var att SAVEPOINT-anropet i sig föll, att
            # ROLLBACK TO aldrig nåddes, och att HELA installationen dog på
            # en graf som bara skulle ha degraderat.
            #
            # Innanför savepointet kostar ett nekat LOAD bara en rollback
            # till sparpunkten — precis den degradering som var avsedd.
            self._load_age_extension()
            # AGE:s operatorer (@>, som MERGE/SET/DELETE och
            # property-matchning använder) är OQUALIFICERADE inuti
            # Cypher-kroppen. Utan ag_catalog i search_path resolvas de
            # inte → "operator does not exist: ag_catalog.agtype @>
            # ag_catalog.agtype". SET LOCAL är transaktionslokalt och
            # läcker inte i anslutningspoolen.
            self.env.cr.execute(
                "SET LOCAL search_path = ag_catalog, public")
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

    def _refuse_unlabelled_match(self, query):
        """Neka `MATCH (n)` utan label — den kraschar PostgreSQL.

        FYND 2026-11-11 (luke18):

            SELECT * FROM ag_catalog.cypher('odoo_mind',
                $$ MATCH (n) RETURN count(n) AS cnt $$)

        dödade servern med **signal 11 (segmentation fault)**. Fem gånger
        på en timme, varje gång med samma fråga i DETAIL-raden. Varje
        krasch tog 4–8 minuters recovery för 22 GB databas.

        En label-lös MATCH tvingar AGE att skanna hela grafen utan index.
        Med 4338 noder i `odoo_mind` faller den i C-lagret — inte som ett
        SQL-fel, utan som en processdöd. Det går inte att fånga med
        try/except; servern är borta innan Python ser svaret.

        Därför nekas frågan HÄR, före `cr.execute`. Ett felmeddelande i
        Odoo är oändligt mycket billigare än en kraschad databas.

        Rätt fråga är label-bunden:
            MATCH (n:OdooPartner) RETURN count(n)
        """
        import re as _re
        # MATCH följt av en nod utan kolon-typ: (n) eller (n {prop: 1})
        for m in _re.finditer(r'\bMATCH\b(.*?)(?=\bRETURN\b|\bWHERE\b|\bSET\b|\bDELETE\b|\bCREATE\b|$)',
                              query, _re.I | _re.S):
            body = m.group(1)
            for node in _re.finditer(r'\(\s*(\w+)?\s*([^)]*)\)', body):
                var, rest = node.group(1) or '', node.group(2)
                if ':' in rest.split('{')[0]:
                    continue  # har label — ok
                if not var and not rest.strip():
                    continue  # () — anonym, men inte vårt mönster
                raise UserError(_(
                    "Cypher-frågan matchar noder utan label, vilket "
                    "kraschar databasen (Apache AGE segfault). "
                    "Ange en label, t.ex. MATCH (n:OdooPartner)."))

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
        """Check if AGE extension is installed and the graph is usable.

        OBS: vi testar INTE med `SELECT 1 FROM ag_catalog.ag_graph`.
        ag_catalog ägs av postgres-superusern, så en vanlig Odoo-roll
        (app-rollen) får `permission denied` på den tabellen — även när
        AGE är installerat och grafen fungerar. Det gjorde att den här
        metoden returnerade False på ett fullt fungerande system, och
        graph_query svarade "AGE may not be installed".

        I stället kör vi en minimal cypher() mot grafen — exakt den väg
        anroparen sedan använder. Fungerar den är grafen användbar.
        """
        try:
            self.env.cr.execute("""
                SELECT 1 FROM pg_extension WHERE extname = 'age'
            """)
            if not self.env.cr.fetchone():
                return False
            # SAVEPOINT: ett fel här får inte förgifta transaktionen
            # (samma mönster som cypher() nedan).
            self.env.cr.execute("SAVEPOINT age_available")
            try:
                self._load_age_extension()
                self.env.cr.execute("""
                    SELECT * FROM ag_catalog.cypher('odoo_mind', $$ RETURN 1 $$)
                    AS (x ag_catalog.agtype)
                """)
                ok = self.env.cr.fetchone() is not None
                self.env.cr.execute("RELEASE SAVEPOINT age_available")
                return ok
            except Exception:
                self.env.cr.execute("ROLLBACK TO SAVEPOINT age_available")
                return False
        except Exception:
            return False
