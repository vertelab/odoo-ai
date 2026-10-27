# -*- coding: utf-8 -*-
"""18.0.1.339 — städa den avvecklade heartbeat-cronen.

`data/cron_heartbeat.xml` tömdes 2026-10-25 och posten togs bort helt. Men
`ir_model_data`-raden blev kvar, så `env.ref()` hittade fortfarande en
`ir.cron` som bara var avaktiverad — och ett test som prövar att väckaren är
BORTA föll.

Raderna raderas här. Cronen är avvecklad; endast cron.task-väckaren ska finnas.
"""

import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return

    cr.execute(
        """
        SELECT res_id FROM ir_model_data
         WHERE module = 'ai_agent_core'
           AND name = 'cron_ai_heartbeat'
           AND model = 'ir.cron'
        """
    )
    row = cr.fetchone()
    if not row:
        _logger.info(
            '18.0.1.339: heartbeat-cronens xmlid saknas — inget att städa')
        return

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
        '18.0.1.339: tog bort den avvecklade heartbeat-cronen (ir.cron id=%s) '
        'och dess xmlid', cron_id)
