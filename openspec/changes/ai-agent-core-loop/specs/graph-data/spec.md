# Spec: Graph Data (odoograph via pgGraph)

## Requirements

### GRAPH-001 [NOW] — Graph backend via pgGraph
The system MUST use pgGraph (PostgreSQL extension) as the graph database backend.
- pgGraph MUST be installed as a PostgreSQL extension (`CREATE EXTENSION graph`)
- Odoo source tables MUST be registered as graph nodes via `graph.add_table()`
- Foreign key relationships MUST be registered as graph edges via `graph.add_edge()`
- The graph index MUST be built and maintained via `graph.build()` and `graph.sync()`
- NO separate graph database (Neo4j, etc.) required — everything in PostgreSQL
- pgGraph runs in the same PostgreSQL instance as Odoo — zero network latency

### GRAPH-002 [NOW] — Graph query tool
The system MUST provide a `graph_query` tool for ai.quest agents.
- `graph_query(cypher, params, language)` — execute Cypher or GQL queries
- Queries run via `graph.cypher()` or `graph.gql()` SQL functions
- Results returned as structured data (JSONB rows)
- Additional methods: `graph_search()`, `shortest_path()`, `traverse()`
- `risk_level`: `read_only` (graph queries never modify data)
- The tool MUST use the authenticated user's PostgreSQL connection

### GRAPH-003 [NOW] — Odoo access rights in graph
The system MUST respect Odoo's access control in graph queries.
- pgGraph's source-table ACL checks MUST verify SELECT privileges before reading data
- Graph queries for a user MUST only return data their PostgreSQL role can access
- The graph does NOT bypass Odoo's security model
- `PG002` errors raised for unauthorized source-table access

### GRAPH-004 [NOW] — Per-user tenant isolation
The system MUST isolate graph data per user/company using pgGraph's tenant mechanism.
- Odoo records: `tenant_column = 'company_id'` → each company sees its own data
- Email graph: `tenant_column = 'user_id'` → each user sees only their own emails
- `graph.tenant_setting` MUST be set at session start based on authenticated user
- Tenant mismatch raises `PG005` — accidental cross-tenant queries are blocked
- Multi-tenant Odoo: companies are fully isolated in the graph

### GRAPH-005 [NOW] — Email graph integration
The system MUST integrate email data into the graph with personal scoping.
- Email nodes: Person, Email, Attachment — registered as graph tables
- Email→Person relationships: SENT, TO, CC — registered as graph edges
- Mail data tenant = email account owner's user_id
- ai.quest agents can traverse "show me all emails from this contact"
- Email graph is personal — never shared across users

### GRAPH-006 [NOW] — Odoo model registration
The system MUST register core Odoo models as graph nodes at installation.
- Minimum registered tables: res_partner, sale_order, account_move, crm_lead, mail_message, product_product
- Edges derived from Odoo foreign keys: partner_id, order_id, product_id, etc.
- Registration MUST be idempotent (safe to re-run)
- New models can be registered via `graph.add_table()` admin function
- Models without graph registration are simply not queryable via graph — ORM still works

### GRAPH-007 [NEXT] — Graph sync from odoograph publisher
The system SHOULD keep the graph in sync with live Odoo changes.
- odoograph publisher polls Odoo for model changes (create/write/unlink)
- Changes written to source tables → `graph.sync()` updates the graph index
- Near-real-time: configurable poll interval (default 30s)
- Alternative: pgGraph's built-in sync policies for automated maintenance

### GRAPH-008 [NEXT] — Graph skill recipes
The system SHOULD provide graph query recipes as ai.skill resources.
- Port Hermes' 10 Cypher queries to `recipes/graph/*.md`
- Recipes: partner_by_email, partner_relations, email_thread, contact_network, partner_activity
- Agents use recipes as skill references, not hard-coded queries
- Recipes are versioned and updatable independently

## Data Model: Graph Registration

```sql
-- Node tables (Odoo models → graph nodes)
SELECT graph.add_table('res_partner', 'id',
  columns => ARRAY['name', 'email', 'company_id', 'create_date'],
  tenant_column => 'company_id'
);
SELECT graph.add_table('sale_order', 'id',
  columns => ARRAY['name', 'amount_total', 'state', 'date_order', 'partner_id'],
  tenant_column => 'company_id'
);
SELECT graph.add_table('account_move', 'id',
  columns => ARRAY['name', 'amount_total', 'state', 'date', 'partner_id'],
  tenant_column => 'company_id'
);

-- Edge tables (FK relationships → graph edges)
SELECT graph.add_edge('placed_by', 'sale_order.partner_id', 'res_partner.id');
SELECT graph.add_edge('invoiced_to', 'account_move.partner_id', 'res_partner.id');

-- Email graph (personal, tenant = user_id)
SELECT graph.add_table('mail_message', 'id',
  columns => ARRAY['subject', 'body', 'date', 'author_id', 'res_id', 'model'],
  tenant_column => 'author_id' -- email owner
);
```

## Non-requirements
- NOT building a separate Neo4j deployment (pgGraph replaces it)
- NOT replicating all Odoo data into the graph (only registered models)
- NOT supporting real-time sync initially (poll-based is sufficient)
- NOT exposing graph admin functions to ai.quest agents (only read queries)
