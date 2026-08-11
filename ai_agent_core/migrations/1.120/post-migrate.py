# -*- coding: utf-8 -*-
"""Migrate to 1.120: per-database AGE/pgvector setup (schema, grants, ownership).

Owns the per-database PostgreSQL configuration that was previously duplicated
in Salt (`postgres.extensions_activate`):

  - CREATE SCHEMA IF NOT EXISTS ag_catalog   — pre-created so grants can be
    applied before `CREATE EXTENSION age`, and so a restricted app role can
    use AGE from the start (age.control declares `schema = 'ag_catalog'`, so
    CREATE EXTENSION reuses an existing schema).
  - CREATE EXTENSION IF NOT EXISTS vector    — pgvector embeddings (OKF)
  - CREATE EXTENSION IF NOT EXISTS age CASCADE — Odoo Mind graph
  - create_graph('odoo_mind') if missing     — idempotent
  - GRANT USAGE, CREATE ON ag_catalog + graph schema → app role (current_user)
  - ownership of AGE system tables/sequences (_ag_label_vertex, _ag_label_edge,
    *_id_seq) → app role

Division of labour with Salt:
  - Salt `postgres.extensions`  : server-side packages + shared_preload_libraries
    + restart (server-wide, once per PG node).
  - This migration (runs via checkmodule --init / module upgrade) : per-database
    setup for new and existing databases.
  - Salt `postgres.extensions_activate` remains only as a backfill for legacy
    databases created before this migration existed.

Idempotent: every statement is guarded or IF NOT EXISTS; failures are non-fatal
(logged as warnings) so the module still installs without AGE/pgvector.
"""

import logging
import re

_logger = logging.getLogger(__name__)

GRAPH_NAME = 'odoo_mind'

_IDENTIFIER_RE = re.compile(r'^[a-zA-Z0-9_$]+$')


def _extension_installed(cr, extname):
    cr.execute("SELECT 1 FROM pg_extension WHERE extname = %s", (extname,))
    return bool(cr.fetchone())


def _schema_exists(cr, schema):
    cr.execute("SELECT 1 FROM pg_namespace WHERE nspname = %s", (schema,))
    return bool(cr.fetchone())


def _relation_exists(cr, schema, relname):
    cr.execute("""
        SELECT 1
          FROM pg_class c
          JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE n.nspname = %s AND c.relname = %s
    """, (schema, relname))
    return bool(cr.fetchone())


def _safe_identifier(name):
    """Return name double-quoted, or None if it does not look like an identifier."""
    if name and _IDENTIFIER_RE.match(name):
        return '"%s"' % name
    return None


def migrate(cr, version):
    _logger.info("Running migration 1.120: AGE/pgvector per-database setup")

    app_user = None
    # Resolve the role Odoo connects as — this is the "app user" that needs
    # access to AGE/pgvector objects.
    cr.execute("SELECT current_user")
    current = cr.fetchone()
    app_user = _safe_identifier(current[0] if current else '')
    if not app_user:
        _logger.warning("Could not resolve app role — skipping grants/ownership")
        app_user = None

    # ── 1. Pre-create ag_catalog so grants can exist before CREATE EXTENSION
    try:
        cr.execute("CREATE SCHEMA IF NOT EXISTS ag_catalog")
        _logger.info("Ensured ag_catalog schema")
    except Exception as e:
        _logger.warning("Could not create ag_catalog schema (non-fatal): %s", e)

    # ── 2. pgvector
    try:
        cr.execute("CREATE EXTENSION IF NOT EXISTS vector")
        _logger.info("Ensured vector extension")
    except Exception as e:
        _logger.warning("vector extension unavailable (non-fatal): %s", e)

    # ── 3. Apache AGE
    try:
        cr.execute("CREATE EXTENSION IF NOT EXISTS age CASCADE")
        _logger.info("Ensured age extension")
    except Exception as e:
        _logger.warning("age extension unavailable (non-fatal): %s", e)

    # ── 4. odoo_mind graph (only if AGE actually got installed)
    if _extension_installed(cr, 'age'):
        try:
            cr.execute(
                "SELECT 1 FROM ag_catalog.ag_graph WHERE name = %s",
                (GRAPH_NAME,))
            if not cr.fetchone():
                cr.execute(
                    "SELECT * FROM ag_catalog.create_graph(%s)",
                    (GRAPH_NAME,))
                _logger.info("Created %s graph", GRAPH_NAME)
            else:
                _logger.info("%s graph already exists", GRAPH_NAME)
        except Exception as e:
            _logger.warning("Graph initialization failed (non-fatal): %s", e)

    # ── 5. Grants + ownership for the app role (harmless if already owner/superuser)
    if app_user:
        if _schema_exists(cr, 'ag_catalog'):
            try:
                cr.execute(
                    "GRANT USAGE, CREATE ON SCHEMA ag_catalog TO %s" % app_user)
            except Exception as e:
                _logger.warning("GRANT ag_catalog failed (non-fatal): %s", e)
        if _schema_exists(cr, GRAPH_NAME):
            try:
                cr.execute(
                    "GRANT USAGE, CREATE ON SCHEMA %s TO %s"
                    % (GRAPH_NAME, app_user))
                cr.execute(
                    "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA %s TO %s"
                    % (GRAPH_NAME, app_user))
                cr.execute(
                    "GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA %s TO %s"
                    % (GRAPH_NAME, app_user))
            except Exception as e:
                _logger.warning("GRANT graph schema failed (non-fatal): %s", e)

        # AGE system tables/sequences are created lazily on first label
        # registration — only transfer ownership of what exists.
        for rel in ('_ag_label_vertex', '_ag_label_edge'):
            if _relation_exists(cr, GRAPH_NAME, rel):
                try:
                    cr.execute(
                        "ALTER TABLE %s.%s OWNER TO %s"
                        % (GRAPH_NAME, rel, app_user))
                except Exception as e:
                    _logger.warning(
                        "ALTER TABLE %s OWNER failed (non-fatal): %s", rel, e)
        for seq in ('_label_id_seq', '_ag_label_vertex_id_seq',
                    '_ag_label_edge_id_seq'):
            if _relation_exists(cr, GRAPH_NAME, seq):
                try:
                    cr.execute(
                        "ALTER SEQUENCE %s.%s OWNER TO %s"
                        % (GRAPH_NAME, seq, app_user))
                except Exception as e:
                    _logger.warning(
                        "ALTER SEQUENCE %s OWNER failed (non-fatal): %s",
                        seq, e)

    _logger.info("Migration 1.120 complete")
