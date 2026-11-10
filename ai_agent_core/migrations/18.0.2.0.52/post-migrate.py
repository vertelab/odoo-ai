# -*- coding: utf-8 -*-
"""Post-migration 18.0.2.0.52: byt artefakttypen 'mål' till 'goal'.

VARFÖR: den OKF-engelska refaktoreringen (87ca23e9) bytte namnet i både
skrivaren (`_okf_upsert(..., 'goal', ...)`) och uppslagningen
(`('artifact_type_id.name', 'in', ['roll', 'goal'])`) — men INTE i
data-XML:en, där posten fortfarande hette `mål`.

Följden var tyst och allvarlig: `_index_user_goals` skrev mål-koncept under
en typ som inte fanns, och L2-injektionen letade efter en typ som inga
koncept hade. Aktiva mål nådde aldrig systemprompten — och ett avslutat mål
kunde aldrig filtreras bort, eftersom ingen rad matchade någotdera.

IDEMPOTENT: byter bara om det gamla namnet finns kvar, och bara när det nya
inte redan upptaget (unik namnkonflikt skulle annars fälla hela
uppgraderingen).
"""

import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    cr.execute(
        "SELECT id FROM ai_artifact_type WHERE name = %s LIMIT 1", ('goal',))
    goal_row = cr.fetchone()
    cr.execute(
        "SELECT id FROM ai_artifact_type WHERE name = %s LIMIT 1", ('mål',))
    old_row = cr.fetchone()
    if not old_row:
        return
    if goal_row:
        # Båda finns: den engelska posten är den levande. Den gamla lämnas
        # orörd — att slå ihop dem skulle kräva att koncept flyttas, och det
        # är en egen fråga.
        _logger.warning(
            "18.0.2.0.52: ai.artifact.type 'goal' finns redan — 'mål' (id=%s) "
            "lämnas orörd", old_row[0])
        return
    cr.execute(
        "UPDATE ai_artifact_type SET name = %s WHERE id = %s",
        ('goal', old_row[0]))
    _logger.info(
        "18.0.2.0.52: ai.artifact.type 'mål' -> 'goal' (id=%s)", old_row[0])
