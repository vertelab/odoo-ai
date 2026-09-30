# -*- coding: utf-8 -*-
"""Migrate to 18.0.1.290: laga OKF-versionskedjan (okf-recall-path 14.6).

`superseded_by_id` sattes aldrig före 14.2, så äldre rader kan vara markerade
`superseded` utan att peka på sin efterträdare — och en förälder kan stå kvar
som `stable` medan en nyare version finns. Detektionen (14.5) hittar dem.

Migrationen är generisk och idempotent: för varje `(scope, concept_key)` sätts
`status='superseded'` + `superseded_by_id` på alla rader med lägre `version` än
max. Innehåll (`summary`, `title`, `embedding`, `entities`) rörs aldrig — bara
livscykelfälten.
"""

import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    _logger.info("Running migration 18.0.1.290: OKF versionskedja")

    # Sätt superseded + superseded_by_id på alla rader som har en nyare version
    # i samma (scope, concept_key). Idempotent: redan korrekta rader rörs inte.
    cr.execute("""
        WITH latest AS (
            SELECT scope, concept_key, MAX(version) AS max_version
              FROM ai_okf_concept
             GROUP BY scope, concept_key
        )
        UPDATE ai_okf_concept AS c
           SET status = 'superseded',
               superseded_by_id = (
                   SELECT n.id FROM ai_okf_concept AS n
                    WHERE n.scope = c.scope
                      AND n.concept_key = c.concept_key
                      AND n.version = l.max_version
                    LIMIT 1
               )
          FROM latest AS l
         WHERE c.scope = l.scope
           AND c.concept_key = l.concept_key
           AND c.version < l.max_version
           AND (c.status != 'superseded' OR c.superseded_by_id IS NULL)
    """)
    _logger.info(
        "Migration 18.0.1.290: %d rader fick superseded + efterträdare",
        cr.rowcount)

    # 14.7: verifiera att ingen förälder står kvar som stable med en nyare version.
    cr.execute("""
        SELECT count(*) FROM ai_okf_concept c
         WHERE EXISTS (
             SELECT 1 FROM ai_okf_concept p
              WHERE p.id = c.supersedes_id AND p.status = 'stable'
         )
    """)
    remaining = cr.fetchone()[0]
    _logger.info(
        "Migration 18.0.1.290: %d rader kvar med stable-förälder (förväntat 0)",
        remaining)
