# -*- coding: utf-8 -*-
"""Migrate to 18.0.1.328: gateway_session_id på ai.coworker.session.

VARFÖR: gateway-anropseffektivitet D1 inför ett stabilt sessions-id för
korrelation mot Bifrost-gatewayens logg. Fältet är additivt med en
Python-default, men en ADD COLUMN utan SQL-default sätter NULL på
befintliga rader — och en session utan gateway-identitet kan inte
korreleras mot gateway-loggen (måtten förblir omätta).

Denna migration fyller `gateway_session_id` för befintliga sessioner med
ett nytt UUID var. Vi kan INTE återanvända `name`: den är en visnings-
etikett satt från prompt/subject, ofta trunkerad, och två sessioner kan
dela samma namn. Ett nytt UUID per rad är den enda korrekta nyckeln.

OBS: sessioner skapade före denna version har gateway-anrop loggade under
den GAMLA nyckeln (prompt-text trunkerad till 64 tecken). De raderna
förblir okopplade — det är avsiktligt (ingen gissning, ingen tystnad).
Migrationen lagar framtiden, inte historiken.

IDEMPOTENT: rader som redan har ett värde lämnas orörda.
KÖRS EFTER schema-uppdateringen (post-migrate), så att kolumnen finns.
"""

import logging
import uuid

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return

    # Hämta rader som saknar gateway-id.
    cr.execute("""
        SELECT id FROM ai_coworker_session
         WHERE gateway_session_id IS NULL OR gateway_session_id = ''
    """)
    rows = cr.fetchall()
    if not rows:
        _logger.info(
            'gateway-identitet-migration: inga sessioner saknade '
            'gateway_session_id')
        return

    for (session_id,) in rows:
        cr.execute("""
            UPDATE ai_coworker_session
               SET gateway_session_id = %s
             WHERE id = %s
               AND (gateway_session_id IS NULL OR gateway_session_id = '')
        """, (str(uuid.uuid4()), session_id))

    _logger.info(
        'gateway-identitet-migration: satte gateway_session_id för %d '
        'sessioner', len(rows))
