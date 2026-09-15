# -*- coding: utf-8 -*-
"""Städa versionskedjor som innehåller identiska versioner (Fas 14).

Varför den här filen finns
--------------------------
`_okf_upsert()` skapade en ny version varje gång den anropades — även när
innehållet var bokstavligen identiskt. `_index_all_personal_sources` är en
cron som kör dagligen, så kedjorna växte med en rad per dygn.

Mätt mot `social` 2026-09-14 (före städning):

    company|partner,10      : 22 rader,  1 unik text
    company|partner,15      : 44 rader,  2 unika texter
    personal|user.2.role    : 22 rader,  1 unik text
    personal|user.6.role    : 22 rader,  1 unik text

Kedjan SÅG ut som en historik men var en logg över att cron hade kört.
Rotorsaken är åtgärdad i `_okf_upsert` (kontrollen `_version_is_unchanged`);
den här migrationen återställer det som redan skrivits.

Varför 'superseded' och inte DELETE
-----------------------------------
`ai.okf.concept` är ADD-only (beslut 10). Att radera vore att bryta
kontraktet OCH förstöra spårbarheten — exakt det som gjorde att felet
kunde leva i veckor utan att synas. Att sätta `status='superseded'` på
dubbletterna är den ärliga representationen: raden finns, den beskrev
samma sak som en senare rad, den är inte längre aktuell.

KONSEKVENS för sökningen: `_okf_search` filtrerar `status != 'superseded'`,
så efter denna körning returnerar varje kedja EN rad (den senaste). Det är
avsiktligt — 22 identiska fulltextposter gav 22 chanser att hitta samma sak.

Härdning (lärdom från migrations/1.11, som svalde sitt ALTER TABLE):
- Ingen bred try/except. Ett fel ska stoppa och synas.
- Idempotent: rör bara rader som är 'stable'/'draft' och har en senare
  version med SAMMA innehåll.
- Räknar och loggar vad den gjorde.
"""

import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    # 1. Inventera före — så att loggen kan verifieras mot en oberoende
    #    psql-fråga efteråt.
    cr.execute("""
        SELECT scope, concept_key, count(*) AS n,
               count(DISTINCT summary) AS unika
        FROM ai_okf_concept
        GROUP BY scope, concept_key
        HAVING count(*) > 1
    """)
    kedjor = cr.fetchall()
    _logger.info(
        'Fas 14: %d versionskedjor med fler än en rad: %s',
        len(kedjor),
        ', '.join('%s|%s=%d rader/%d unika' % k for k in kedjor) or '(inga)')

    # 2. Markera varje rad som har en SENARE version med samma
    #    (title, summary, source_ref) som superseded.
    #
    #    `IS NOT DISTINCT FROM` i stället för `=`: fälten är NULL-bara, och
    #    NULL = NULL är NULL (inte true) i SQL. Med `=` hade rader med NULL
    #    title aldrig städats — och det är precis de kedjor vi ska åtgärda
    #    (user.*.role har title satt, men partner-raderna har NULL source_ref).
    #
    #    Villkoret "senare version" är `b.version > a.version`, inte
    #    create_date: version är kedjans egen ordning.
    cr.execute("""
        UPDATE ai_okf_concept a
        SET status = 'superseded'
        WHERE a.status IN ('stable', 'draft')
          AND EXISTS (
              SELECT 1 FROM ai_okf_concept b
              WHERE b.scope = a.scope
                AND b.concept_key = a.concept_key
                AND b.version > a.version
                AND b.title IS NOT DISTINCT FROM a.title
                AND b.summary IS NOT DISTINCT FROM a.summary
                AND b.source_ref IS NOT DISTINCT FROM a.source_ref
          )
    """)
    städade = cr.rowcount
    _logger.info(
        'Fas 14: %d dubblettrader markerade som superseded '
        '(innehållsligt identiska med en senare version).', städade)

    # 3. Verifiera resultatet — inte bara att UPDATE körde.
    cr.execute("""
        SELECT scope, concept_key, count(*) AS n,
               count(*) FILTER (WHERE status = 'superseded') AS sup
        FROM ai_okf_concept
        GROUP BY scope, concept_key
        HAVING count(*) > 1
        ORDER BY n DESC
    """)
    for scope, key, n, sup in cr.fetchall():
        aktuella = n - sup
        _logger.info(
            'Fas 14: %s|%s → %d rader, %d superseded, %d aktuell(a)',
            scope, key, n, sup, aktuella)
        if aktuella > 1:
            # Inte ett fel vi kan reparera här (kan vara genuina ändringar),
            # men det ska SYNAS att kedjan fortfarande har flera aktuella.
            _logger.warning(
                'Fas 14: %s|%s har fortfarande %d aktuella rader — '
                'granska manuellt.', scope, key, aktuella)

    _logger.info('Fas 14 klar: %d rader städade.', städade)
