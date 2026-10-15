# -*- coding: utf-8 -*-
"""Post-migration 18.0.1.331: avveckla den gamla heartbeat-cronen.

VARFÖR: `data/cron_heartbeat.xml` deklarerar `cron_ai_heartbeat` med
`active=False`, men posten ligger i ett `noupdate="1"`-block. Odoos
`noupdate` betyder "skriv bara vid FÖRSTA installationen" — vid uppgradering
rörs posten inte alls. På en befintlig databas (t.ex. luke18) förblir
cronen därför `active=True`.

Konsekvensen är exakt den dubbla väckning som schemalagt-arbete D9 skulle
ta bort: både `cron_ai_heartbeat` (ir.cron, varje minut) och den nya
`ai_heartbeat_dispatch` (cron.task, cadence min) väcker coworker-listan.

VARFÖR POST-migrering och inte pre: den nya `cron.task`-posten skapas av
data-XML under moduluppgraderingen. Vi vill stänga av den gamla EFTER att
den nya finns på plats, så det aldrig finns ett fönster utan väckare.

VARFÖR inte bara ta bort `noupdate`: då skulle HELA cron-posten skrivas om
vid varje uppgradering, inklusive `code` och `interval_*` — och en post som
en kund medvetet justerat skulle tystas. Vi vill bara säkerställa
`active=False` på just denna avvecklade post.

IDEMPOTENT: att sätta `active=False` på en redan inaktiv post är en no-op.
Saknas posten (nyinstallation där data-XML redan skrev den inaktiv, eller
en databas där xmlid:n städats) görs ingenting.
"""

import logging

_logger = logging.getLogger(__name__)

# Xmlid för den avvecklade cronen. Läses via ir.model.data, inte env.ref,
# eftersom migrationen körs på en rå cursor utan ORM-cache.
XMLID_MODULE = 'ai_agent_core'
XMLID_NAME = 'cron_ai_heartbeat'


def migrate(cr, version):
    cr.execute(
        """
        SELECT res_id
          FROM ir_model_data
         WHERE module = %s
           AND name = %s
           AND model = 'ir.cron'
        """,
        (XMLID_MODULE, XMLID_NAME),
    )
    row = cr.fetchone()
    if not row:
        _logger.info(
            'Post-migration 18.0.1.331: %s.%s saknas — inget att avveckla',
            XMLID_MODULE, XMLID_NAME)
        return

    cron_id = row[0]
    cr.execute(
        """
        UPDATE ir_cron
           SET active = false
         WHERE id = %s
           AND active = true
        """,
        (cron_id,),
    )
    deactivated = cr.rowcount
    if deactivated:
        _logger.info(
            'Post-migration 18.0.1.331: avvecklade den gamla heartbeat-cronen '
            '(ir.cron id=%s) — endast cron.task-väckaren väcker nu',
            cron_id)
    else:
        _logger.info(
            'Post-migration 18.0.1.331: heartbeat-cronen (id=%s) var redan '
            'inaktiv', cron_id)
