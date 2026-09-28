# -*- coding: utf-8 -*-
"""Migrate to 18.0.1.261: source_user_id på ai.okf.concept + backfill.

Bakgrund
--------
`_okf_search` filtrerade bara på `scope`, aldrig på ägaren. Ett
personal-scope returnerade ALLA användares personliga koncept; ett
coworker-scope ALLA coworkers. Access-lagret (`_resolve_visible_sources`)
täcker inte gapet — det prövar källpostens läsbarhet, inte konceptets ägare.

Konsekvens: en användares personliga minne läckte in i en annan användares
systemprompt (odoo-mind-memory-scope-isolation).

Vad migrationen gör
-------------------
1. Lägger `source_user_id` på `ai.okf.concept` (ORM:en skapar kolumnen,
   men vi säkerställer den explicit för halvuppgraderade system).
2. Backfillar `source_user_id` för `coworker`-koncept vars `source_ref`
   pekar på en session (`ai.coworker.session,<id>`): användaren hämtas ur
   sessionens `user_id`.

Varför backfill
---------------
Utan backfill skulle befintliga coworker-koncept behandlas som
coworker-globala (source_user_id tomt) och injiceras för alla användare —
dvs. läckaget består för historiken. Backfillen stänger det.

Koncept utan sessionsreferens lämnas som coworker-globala: det är den
ärliga representationen (vi vet inte vem som lärde dem).
"""

import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    _logger.info('Running migration 18.0.1.261: source_user_id (scope-'
                 'isolering)')

    # 1. Säkerställ kolumnen (ORM:en gör det normalt, men explicit är säkrare).
    cr.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'ai_okf_concept'
          AND column_name = 'source_user_id'
    """)
    if not cr.fetchone():
        cr.execute("""
            ALTER TABLE ai_okf_concept
            ADD COLUMN source_user_id integer
            REFERENCES res_users(id) ON DELETE SET NULL
        """)
        _logger.info('OKF: lade till kolumnen source_user_id')
    else:
        _logger.info('OKF: source_user_id finns redan')

    # 2. Backfill från sessionsreferens.
    cr.execute("""
        UPDATE ai_okf_concept c
        SET source_user_id = s.user_id
        FROM ai_coworker_session s
        WHERE c.scope = 'coworker'
          AND c.source_user_id IS NULL
          AND c.source_ref = 'ai.coworker.session,' || s.id
          AND s.user_id IS NOT NULL
    """)
    filled = cr.rowcount
    _logger.info('OKF: backfillade source_user_id på %s coworker-koncept',
                 filled)

    # 3. Rapportera hur många coworker-koncept som förblir globala.
    cr.execute("""
        SELECT count(*) FROM ai_okf_concept
        WHERE scope = 'coworker' AND source_user_id IS NULL
          AND archived = false
    """)
    _logger.info('OKF: %s coworker-koncept utan känd källa (förblir globala)',
                 cr.fetchone()[0])
