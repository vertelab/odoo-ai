# -*- coding: utf-8 -*-
"""18.0.2.0.19 — ta bort det avvecklade fältet ai.coworker.is_supervisor.

Fältet var readonly och synkades bara av en @api.onchange. Vid dataimport
(default_coworker.xml, hooks.py) kör onchange aldrig, så fältet stod kvar på
False trots orchestration_mode='supervisor'.

Konsekvensen var att web-chatten (controllers/stream.py:510) läste
`quest.is_supervisor` → False → `gen_is_supervisor` blev False → supervisor-
loopen byggdes aldrig. Folke, Sigrid och Allmän assistent körde därför alltid
bara sin FÖRSTA agent; Vera Sjöberg, Odoo-specialist, Research m.fl. anropades
aldrig. Inget fel loggades — det såg bara ut som en svagare assistent.

`orchestration_mode` är nu den enda sanningen. Kolumnen droppas.
"""

import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return

    # Säkerställ att inget supervisor-läge går förlorat: alla poster som hade
    # is_supervisor=True men saknar orchestration_mode sätts till 'supervisor'
    # INNAN kolumnen försvinner.
    try:
        cr.execute("""
            UPDATE ai_coworker
               SET orchestration_mode = 'supervisor'
             WHERE is_supervisor IS TRUE
               AND (orchestration_mode IS NULL OR orchestration_mode = 'single')
        """)
        if cr.rowcount:
            _logger.info(
                'is_supervisor-borttagning: %s coworker(s) fick '
                "orchestration_mode='supervisor'", cr.rowcount)
    except Exception as e:
        _logger.warning('Kunde inte bevara is_supervisor-läget: %s', e)

    try:
        cr.execute("ALTER TABLE ai_coworker DROP COLUMN IF EXISTS is_supervisor")
        _logger.info('ai_coworker.is_supervisor borttagen')
    except Exception as e:
        _logger.warning('Kunde inte droppa ai_coworker.is_supervisor: %s', e)
