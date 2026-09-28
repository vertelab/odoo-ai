# -*- coding: utf-8 -*-
"""Backfill av ai.coworker.hitl.session_id (ai-coworker-hitl §7).

Varför den här filen finns
--------------------------
`session_id` läggs till som OBLIGATORISKT fält på `ai.coworker.hitl`.
PostgreSQL kan inte lägga till en NOT NULL-kolumn med FK på en tabell som
redan har rader utan att först fylla den — ALTER TABLE faller annars på
`column "session_id" contains null values`.

Fältet är inte kosmetiskt: en HITL-request utan session är en herrelös
förfrågan som ingen kan härleda till ett arbete (design D7). Därför
backfillar vi i stället för att tillåta NULL.

Vad vi backfillar MED — och varför det är ärligt
------------------------------------------------
Det finns ingen tidigare koppling att återställa; fältet har aldrig
funnits. Vi kan bara härleda en rimlig session ur det vi vet:

1. Coworkerns session med samma `user_id` (godkännaren) som skapades
   närmast FÖRE requesten. Det är den körning som med största sannolikhet
   begärde godkännandet.
2. Finns ingen sådan: skapa INGEN — lämna raden och logga den som
   ofullständig (se nedan).

Rad 2 är viktig. Att hitta på en session vore att fabricera ett arbete.
Men eftersom fältet är required måste VARJE rad ha ett värde. Lösningen:
skapa en ärligt namngiven platshållarsession per coworker för de rader
som inte kan härledas — den syns i UI:t och kan städas, i stället för att
ljuga med en trovärdig men felaktig koppling.

Härdning (lärdom från migrations/1.11, som svalde sitt ALTER TABLE):
- Ingen bred try/except. Ett fel ska stoppa och synas.
- Verifierar EFTERÅT att kolumnen finns och att inga NULL kvarstår —
  och failar högt om så inte är fallet.
- Idempotent: rör bara rader där session_id IS NULL.

Körs som POST-migrate: Odoo lägger till kolumnen ur modellens
fältdefinition i sitt schema-steg, som ligger FÖRE post-migrate.
"""

import logging

_logger = logging.getLogger(__name__)

PLACEHOLDER_NAME = 'Okänd körning (migrerad)'


def migrate(cr, version):
    # 1. Finns kolumnen redan? (t.ex. efter en avbruten tidigare körning.)
    #    Då är det här en no-op — men vi verifierar ändå i steg 4.
    cr.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'ai_coworker_hitl'
          AND column_name = 'session_id'
    """)
    if not cr.fetchone():
        # Odoo lägger till kolumnen i sitt eget schema-steg FÖRE post-migrate.
        # Om vi hamnar här är filen felplacerad — det ska inte tystas.
        raise Exception(
            'session_id-kolumnen finns inte vid post-migrate. '
            'Odoo borde ha lagt till den ur modellens fältdefinition — '
            'kontrollera att fältet finns i ai_coworker_hitl.py.')

    # 2. Inventera före.
    cr.execute("""
        SELECT count(*) FROM ai_coworker_hitl WHERE session_id IS NULL
    """)
    saknar = cr.fetchone()[0]
    _logger.info('HITL session_id: %d rader saknar session före backfill.',
                 saknar)
    if not saknar:
        _logger.info('HITL session_id: inget att göra.')
        return

    # 3a. Härled: coworkerns session med samma godkännare, skapad närmast
    #     före requesten. DISTINCT ON ger en rad per hitl.
    cr.execute("""
        UPDATE ai_coworker_hitl h
        SET session_id = s.id
        FROM (
            SELECT DISTINCT ON (hitl.id) hitl.id AS hitl_id, sess.id AS id
            FROM ai_coworker_hitl hitl
            JOIN ai_coworker_session sess
              ON sess.coworker_id = hitl.coworker_id
             AND sess.user_id = hitl.user_id
             AND sess.create_date <= hitl.create_date
            WHERE hitl.session_id IS NULL
            ORDER BY hitl.id, sess.create_date DESC
        ) s
        WHERE h.id = s.hitl_id
    """)
    härledda = cr.rowcount
    _logger.info('HITL session_id: %d rader härledda ur coworker+user.',
                 härledda)

    # 3b. Resten: platshållarsession per coworker (ärligt namngiven).
    cr.execute("""
        SELECT DISTINCT coworker_id
        FROM ai_coworker_hitl
        WHERE session_id IS NULL
    """)
    kvarvarande_coworkers = [r[0] for r in cr.fetchall()]

    for coworker_id in kvarvarande_coworkers:
        cr.execute("""
            INSERT INTO ai_coworker_session
                (name, coworker_id, status, create_date, write_date,
                 create_uid, write_uid)
            VALUES (%s, %s, 'done', now(), now(), 1, 1)
            RETURNING id
        """, (PLACEHOLDER_NAME, coworker_id))
        sess_id = cr.fetchone()[0]
        cr.execute("""
            UPDATE ai_coworker_hitl
            SET session_id = %s
            WHERE coworker_id = %s AND session_id IS NULL
        """, (sess_id, coworker_id))
        _logger.info(
            'HITL session_id: coworker %s → platshållarsession %s '
            '(%d rader).', coworker_id, sess_id, cr.rowcount)

    # 4. Verifiera — inte bara att UPDATE körde. Faila högt.
    cr.execute("""
        SELECT count(*) FROM ai_coworker_hitl WHERE session_id IS NULL
    """)
    kvar = cr.fetchone()[0]
    if kvar:
        raise Exception(
            'HITL session_id-backfill misslyckades: %d rader har '
            'fortfarande ingen session. Fältet är required — att fortsätta '
            'vore att lämna herrelösa requests.' % kvar)

    cr.execute("SELECT count(*) FROM ai_coworker_hitl")
    totalt = cr.fetchone()[0]
    _logger.info(
        'HITL session_id klar: %d/%d rader har session '
        '(%d härledda, %d via platshållare).',
        totalt, totalt, härledda, totalt - härledda)
