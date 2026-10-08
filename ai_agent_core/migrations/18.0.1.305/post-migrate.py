# -*- coding: utf-8 -*-
"""Migrate to 18.0.1.305: cron-xmlid `cron_scheduled_quests` → `cron_scheduled_coworkers`.

VARFÖR: modellen `ai.quest` döptes om till `ai.coworker` (2026-07-29), men
cron-filen behöll sitt gamla namn. Filen, xmlid:t och cron-namnet säger nu
`coworkers` — men den befintliga raden i `ir_model_data` pekar på det gamla
xmlid:t. Utan denna migration skulle Odoo:

  1. skapa en NY cron (nytt xmlid) och
  2. lämna den gamla cronen kvar som en föräldralös dubblett — båda skulle
     köra samma jobb var 5:e minut.

Migrationen byter namn på xmlid:t PÅ PLATS, så samma cron-rad (och samma
`ir.actions.server`) behålls. Ingen data skapas eller raderas.

TEKNISKA FÄLLOR (verifierade mot luke18 innan denna skrevs):
  - `ir.cron` ärver namn/modell/kod från sin `ir.actions.server` (delegate).
    Cronens visningsnamn kommer från `ir_act_server.name`.
  - Tabellen heter `ir_act_server` — INTE `ir_actions_server`.
  - `ir_act_server.name` är **jsonb** i Odoo 18 (translaterbart fält), inte
    text. Värdet ser ut som `{"en_US": "..."}`. Ett naket strängvärde ger
    `invalid input syntax for type json`.

IDEMPOTENT: är xmlid:t redan bytt (eller finns inte) görs ingenting.
"""

import json
import logging

_logger = logging.getLogger(__name__)

RENAMES = [
    ('cron_scheduled_quests', 'cron_scheduled_coworkers'),
    ('cron_scheduled_quests_ir_actions_server',
     'cron_scheduled_coworkers_ir_actions_server'),
]

OLD_CRON_NAME = 'AI: Run Scheduled Quests'
NEW_CRON_NAME = 'AI: Run Scheduled Coworkers'


def migrate(cr, version):
    _logger.info(
        "Running migration 18.0.1.305: cron-xmlid quests -> coworkers")

    # 1. Byt xmlid på plats — behåller samma res_id (cronen/actionen).
    for old, new in RENAMES:
        cr.execute("""
            SELECT id FROM ir_model_data
             WHERE module = 'ai_agent_core' AND name = %s
        """, (old,))
        if not cr.fetchone():
            _logger.info('xmlid %s saknas — inget att byta', old)
            continue
        # Om det nya redan finns (t.ex. omskörd körning) — rör inget.
        cr.execute("""
            SELECT 1 FROM ir_model_data
             WHERE module = 'ai_agent_core' AND name = %s
        """, (new,))
        if cr.fetchone():
            _logger.info('xmlid %s finns redan — hoppar över', new)
            continue
        cr.execute("""
            UPDATE ir_model_data SET name = %s
             WHERE module = 'ai_agent_core' AND name = %s
        """, (new, old))
        _logger.info('Bytt xmlid %s -> %s', old, new)

    # 2. Byt cron-namnet. `res.config.settings` slår upp cronen på
    #    `cron_name`, som är related till server actionens namn. Namnet
    #    ligger i en jsonb-kolumn (translaterbart) — byt en_US-värdet och
    #    behåll eventuella andra språk orörda.
    cr.execute("""
        SELECT id, name FROM ir_act_server
         WHERE id IN (
            SELECT res_id FROM ir_model_data
             WHERE module = 'ai_agent_core'
               AND name = 'cron_scheduled_coworkers_ir_actions_server'
         )
    """)
    row = cr.fetchone()
    if not row:
        _logger.info('server action saknas — inget namn att byta')
        return

    action_id, name = row
    # name kan vara jsonb (dict) eller redan en sträng beroende på drivrutin.
    if isinstance(name, str):
        try:
            name = json.loads(name)
        except (ValueError, TypeError):
            name = None
    if not isinstance(name, dict):
        name = {}
    if name.get('en_US') == NEW_CRON_NAME:
        _logger.info('server action-namnet är redan %r', NEW_CRON_NAME)
        return
    name['en_US'] = NEW_CRON_NAME
    cr.execute(
        "UPDATE ir_act_server SET name = %s WHERE id = %s",
        (json.dumps(name), action_id),
    )
    _logger.info(
        'ir.actions.server %s namn satt till %r', action_id, NEW_CRON_NAME)
