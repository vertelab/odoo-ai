# -*- coding: utf-8 -*-
"""Migrate to 18.0.1.321: handover-fält på ai.coworker.session.

VARFÖR: pi-session-handover lägger till handover-tillståndet på sessionen.
Fälten är additiva och har defaultvärden, men `pi_session_ids` behöver
seedas: den ska bära historiken över samtliga Pi-sessioner som arbetat i
sessionen, och för befintliga sessioner är den enda kända Pi-sessionen
den som `pi_session_id` redan pekar på.

Utan seedningen skulle en session som skapats före denna version få en
tom `pi_session_ids` — och därmed tappa sin egen historik så snart en
handover sker (claimen appendar till en tom lista och den ursprungliga
Pi-sessionen försvinner ur historiken).

IDEMPOTENT: sessioner som redan har `pi_session_ids` satt lämnas orörda.
Sessioner utan `pi_session_id` (t.ex. cron-sessioner som aldrig kopplats
till en Pi-klient) lämnas också orörda — de har ingen Pi-session att
ärva.

KÖRS EFTER schema-uppdateringen (post-migrate), så att kolumnen finns.
"""

import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return

    # Seeda pi_session_ids från pi_session_id — bara där den är tom och
    # det finns ett pi_session_id att ärva.
    cr.execute("""
        UPDATE ai_coworker_session
           SET pi_session_ids = pi_session_id
         WHERE (pi_session_ids IS NULL OR pi_session_ids = '')
           AND pi_session_id IS NOT NULL
           AND pi_session_id != ''
    """)
    seeded = cr.rowcount
    if seeded:
        _logger.info(
            'handover-migration: seedade pi_session_ids för %d sessioner',
            seeded)

    # handover_state har default 'none' i modellen, men en ADD COLUMN utan
    # default sätter NULL på befintliga rader. Sätt dem explicit så att
    # listan (_handover_suggestions filtrerar på = 'none') inte tappar
    # äldre sessioner.
    cr.execute("""
        UPDATE ai_coworker_session
           SET handover_state = 'none'
         WHERE handover_state IS NULL
    """)
    normalized = cr.rowcount
    if normalized:
        _logger.info(
            'handover-migration: satte handover_state=none för %d sessioner',
            normalized)
