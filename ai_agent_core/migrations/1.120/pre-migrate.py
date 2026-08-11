# -*- coding: utf-8 -*-
"""Migrate to 1.120 (pre): ensure AGE/pgvector exist BEFORE model init.

Runs in the pre stage — before the module's models are initialized. Some
models (e.g. OKF with PgVector columns, and anything touching AGE types)
need the `vector` extension, the `ag_catalog` schema and the `age`
extension to already exist when `_auto_init` creates their columns.

- CREATE SCHEMA IF NOT EXISTS ag_catalog   — pre-created so grants can be
  applied before CREATE EXTENSION age, and so CREATE EXTENSION age reuses
  it (age.control declares `schema = 'ag_catalog'`).
- CREATE EXTENSION IF NOT EXISTS vector    — pgvector embeddings (OKF)
- CREATE EXTENSION IF NOT EXISTS age CASCADE — Odoo Mind graph

checkmodule (since 18.0.1.120 tooling) also creates these right after
creating the database; this pre-migration is the module-native guarantee
for upgrades and any other install path. Salt postgres.extensions remains
the server-level package/preload installer.

Idempotent. Failures are re-raised for CREATE EXTENSION (the module cannot
function without them) but with a clear log message.
"""

import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    _logger.info("Running pre-migration 1.120: ensure AGE/pgvector before model init")

    try:
        cr.execute("CREATE SCHEMA IF NOT EXISTS ag_catalog")
        _logger.info("Ensured ag_catalog schema")
    except Exception as e:
        _logger.warning("Could not create ag_catalog schema: %s", e)

    for ext, extra in (('vector', ''), ('age', 'CASCADE')):
        try:
            cr.execute("CREATE EXTENSION IF NOT EXISTS %s %s" % (ext, extra))
            _logger.info("Ensured %s extension", ext)
        except Exception as e:
            _logger.error(
                "Could not create extension %s — install it first "
                "(salt postgres.extensions or as postgres superuser): %s",
                ext, e)
            raise
