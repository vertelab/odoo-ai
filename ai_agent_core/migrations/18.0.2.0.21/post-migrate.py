# -*- coding: utf-8 -*-
"""18.0.2.0.21 — ai.memory på pgvector (ai-memory-pgvector T1.3).

`post_init_hook` körs bara vid INSTALLATION, inte vid `--update`. Utan
denna migration skulle en uppgradering av ett redan installerat system
aldrig få `search_vector`, GIN-indexet eller ivfflat-indexet — kolumnerna
skulle saknas medan modellen såg ut att vara uppgraderad.

Samma fälla som migration 1.11: den körde `ALTER TABLE` före ORM:en
skapat tabellen och svalde felet med `except: _logger.warning(...)`.
Loggen sa "klart" medan kolumnen aldrig uppstod. Här propagerar vi.

Funktionen är idempotent — den kontrollerar information_schema/pg_indexes
före varje steg — så den kan köras om utan att skada något.
"""

import logging

from odoo.addons.ai_agent_core.hooks import (
    memory_ensure_search_infrastructure,
)

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return

    env = None
    try:
        from odoo.api import Environment
        from odoo import SUPERUSER_ID
        env = Environment(cr, SUPERUSER_ID, {})
    except Exception as exc:  # pragma: no cover
        _logger.warning(
            '18.0.2.0.21: kunde inte skapa Environment (%s) — '
            'hoppar över minnesinfrastrukturen', exc)
        return

    # Kolumnerna kräver att tabellerna finns. Vid en färsk installation
    # har ORM:en precis byggt dem; vid uppgradering fanns de redan.
    cr.execute("""
        SELECT 1 FROM information_schema.tables
        WHERE table_name = 'ai_memory' AND table_schema = 'public'
    """)
    if not cr.fetchone():
        _logger.warning(
            '18.0.2.0.21: ai_memory-tabellen finns inte — '
            'minnesinfrastrukturen hoppas över')
        return

    # Kastar vid fel (till skillnad från migration 1.11) — en tyst tom
    # sökväg är värre än ett högljutt fel.
    memory_ensure_search_infrastructure(env)
    _logger.info('18.0.2.0.21: minnesinfrastruktur säkerställd')

    # ── memory_type: 'faiss' är ett lagringsformat, inte en källa ──
    # ai-memory-pgvector T6.1: valet beskriver var innehållet kom ifrån.
    # Befintliga faiss-rader mappas till 'text' EFTERSOM deras innehåll
    # antingen finns i `content` eller ska dras ur FAISS-indexet av
    # re-embedding-cronen. Antalet loggas så att en artefakt utan
    # källfil syns i loggen i stället för att tyst bli osökbar.
    cr.execute("""
        SELECT count(*) FROM ai_memory WHERE memory_type = 'faiss'
    """)
    n_faiss = cr.fetchone()[0]
    if n_faiss:
        cr.execute("""
            SELECT count(*) FROM ai_memory
            WHERE memory_type = 'faiss' AND content IS NOT NULL
        """)
        n_med_content = cr.fetchone()[0]
        _logger.info(
            '18.0.2.0.21: %d faiss-artefakter, varav %d har content. '
            'De övriga (%d) kräver utdrag ur FAISS-indexet — se '
            'ai_memory_reembed-cronen.',
            n_faiss, n_med_content, n_faiss - n_med_content)
