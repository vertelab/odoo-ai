# -*- coding: utf-8 -*-
"""18.0.1.338 — kollapsa OKF-versioner till en rad per concept_key.

Bakgrund: `ai.okf.concept` var ADD-only med en `superseded`-kedja. I drift
blev kedjan en logg over cron-korningar, inte over andringar:

    dms,1     49 rader, en per dygn, identiskt innehall
    website,4 49 rader, samma
    crm.lead  732 nycklar med tva rader var

Modellen ar nu en SPEGEL av kallan: ett koncept per (scope, agare,
concept_key). Den har migreringen behaller den SENASTE raden per nyckel
(hogsta version) och raderar resten.

Historiken finns kvar i kallan (document.page.history, mail.message pa
project.task, dms-filversioner) -- den duplicerades aldrig har.
"""

import logging

from odoo import api, SUPERUSER_ID

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return

    cr.execute("""
        SELECT count(*) FROM ai_okf_concept
    """)
    before = cr.fetchone()[0]

    # Behåll högsta version per (scope, owner_*, concept_key).
    # NULL i ägarfälten jämförs inte med = i SQL, så vi använder
    # IS NOT DISTINCT FROM via en hjälptabell.
    cr.execute("""
        CREATE TEMP TABLE _okf_keep AS
        SELECT DISTINCT ON (
                   scope,
                   owner_company_id,
                   owner_user_id,
                   owner_coworker_id,
                   concept_key
               ) id
        FROM ai_okf_concept
        ORDER BY scope,
                 owner_company_id NULLS LAST,
                 owner_user_id NULLS LAST,
                 owner_coworker_id NULLS LAST,
                 concept_key,
                 version DESC,
                 id DESC
    """)
    cr.execute("SELECT count(*) FROM _okf_keep")
    keep = cr.fetchone()[0]

    # Tag-relationer för de rader som ska bort
    cr.execute("""
        DELETE FROM ai_okf_concept_tag_rel
        WHERE concept_id NOT IN (SELECT id FROM _okf_keep)
    """)
    tags_deleted = cr.rowcount

    # Rader som pekar på borttagna rader (FK) — nolla först
    cr.execute("""
        UPDATE ai_okf_concept
        SET superseded_by_id = NULL
        WHERE superseded_by_id IS NOT NULL
          AND superseded_by_id NOT IN (SELECT id FROM _okf_keep)
    """)
    cr.execute("""
        UPDATE ai_okf_concept
        SET supersedes_id = NULL
        WHERE supersedes_id IS NOT NULL
          AND supersedes_id NOT IN (SELECT id FROM _okf_keep)
    """)

    cr.execute("""
        DELETE FROM ai_okf_concept
        WHERE id NOT IN (SELECT id FROM _okf_keep)
    """)
    deleted = cr.rowcount

    cr.execute("DROP TABLE _okf_keep")

    cr.execute("SELECT count(*) FROM ai_okf_concept")
    after = cr.fetchone()[0]

    # Alla kvarvarande rader är den aktuella speglingen
    cr.execute("UPDATE ai_okf_concept SET status = 'stable' WHERE status = 'superseded'")

    # ── Avvecklad heartbeat-cron (18.0.1.331 lämnade spåret kvar) ──────────
    #
    # `data/cron_heartbeat.xml` tömdes 2026-10-25 och posten togs bort helt.
    # Men `ir_model_data`-raden blev kvar, så `env.ref()` hittade fortfarande
    # en `ir.cron` som bara var avaktiverad — och ett test som prövar att
    # väckaren är BORTA föll.
    #
    # Raden raderas här. Cronen är avvecklad; endast cron.task-väckaren ska
    # finnas kvar.
    cr.execute(
        """
        SELECT res_id FROM ir_model_data
         WHERE module = 'ai_agent_core'
           AND name = 'cron_ai_heartbeat'
           AND model = 'ir.cron'
        """
    )
    row = cr.fetchone()
    if row:
        cron_id = row[0]
        cr.execute("DELETE FROM ir_cron WHERE id = %s", (cron_id,))
        cr.execute(
            """
            DELETE FROM ir_model_data
             WHERE module = 'ai_agent_core'
               AND name = 'cron_ai_heartbeat'
               AND model = 'ir.cron'
            """
        )
        _logger.info(
            'OKF 18.0.1.338: tog bort den avvecklade heartbeat-cronen '
            '(ir.cron id=%s) och dess xmlid', cron_id)
    else:
        _logger.info(
            'OKF 18.0.1.338: heartbeat-cronens xmlid saknas — inget att städa')

    _logger.info(
        'OKF 18.0.1.338: kollapsade %s rader till %s (behöll %s, raderade %s, '
        '%s tagg-relationer bort)',
        before, after, keep, deleted, tags_deleted)
