# -*- coding: utf-8 -*-
"""Migrate to 18.0.1.279: bevakning-over-tid (prompt per init_type + frigjorda uppgifter).

1. `job_prompt` på `ai.coworker.init_type` sätts från coworkerns `description`
   för befintliga `cron`-rader (task 1.3) — annars skulle en aktiv cron-typ
   sakna prompt och körningen nekas.
2. Frigör uppgifter som står kvar utcheckade utan en avslutad körning
   (task 8.1) — dagens bugg lämnade dem låsta för evigt.
"""

import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    _logger.info("Running migration 18.0.1.279: job_prompt + frigjorda uppgifter")

    # 1. job_prompt för cron-rader utan värde: ärv coworkerns description.
    cr.execute("""
        UPDATE ai_coworker_init_type AS it
           SET job_prompt = c.description
          FROM ai_coworker AS c
         WHERE it.coworker_id = c.id
           AND it.init_type = 'cron'
           AND (it.job_prompt IS NULL OR it.job_prompt = '')
           AND c.description IS NOT NULL
           AND c.description != ''
    """)
    _logger.info("Migration 18.0.1.279: %d cron-init_typer fick job_prompt",
                 cr.rowcount)

    # 2. Frigör övergivna utcheckningar: utcheckad men ingen aktiv session
    #    kopplad, eller sessionen är stängd.
    cr.execute("""
        UPDATE ai_org_task AS t
           SET checkout_lock = FALSE,
               checked_out_at = NULL,
               status = 'todo'
         WHERE t.checkout_lock = TRUE
           AND NOT EXISTS (
               SELECT 1 FROM ai_coworker_session AS s
                WHERE s.ai_task_id = t.id
                  AND s.status = 'active'
           )
    """)
    _logger.info("Migration 18.0.1.279: %d övergivna utcheckningar frigjorda",
                 cr.rowcount)
