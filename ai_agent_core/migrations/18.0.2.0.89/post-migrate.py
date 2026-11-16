# -*- coding: utf-8 -*-
"""Post-migration 18.0.2.0.89: `is_key_required=True` på den seedade Bifrost-raden.

DEFEKTEN
--------
`data/provider_bifrost.xml` seedade Bifrost-providern med
`is_key_required=False`. Formuläret dolde nyckelfältet för den flaggan
(`invisible="not is_key_required"`), så en Bifrost-provider utan nyckel på
recordet kunde inte få en — och det gick inte att se att den saknade en.
Anropet gick ut utan `X-Virtual-Key` och gatewayen svarade 403
"combo is not entitled for this virtual key": ett fel som ser ut som en
rättighetsfråga men är en tom sträng.

VARFÖR EN MIGRATION
-------------------
Datafilerna är `noupdate="1"`. En ändrad XML når bara en databas där posten
ännu inte finns — en redan installerad databas behåller sitt gamla värde.
Utan den här migrationen hade nyinstallation fått `True` och uppgraderad
installation `False`: exakt den tysta divergens som gjorde att felet kunde
uppstå från början.

VAD MIGRATIONEN GÖR
-------------------
Sätter `is_key_required = True` på den seedade Bifrost-providern
(`ir.model.data` → `ai_agent_core.provider_bifrost`) när den står på `False`.

VAD DEN INTE GÖR
----------------
- Rör inte `api_key`. Nyckeln ägs av drift/UI (eller löses ur
  `ai_provider_api_key` i odoo.conf vid anropet) — aldrig av en migration.
- Rör inte någon annan provider. `provider_ollama.xml` ska fortsatt vara
  `False`: den providern kräver ingen nyckel.
- Är idempotent: andra körningen hittar redan `True` och gör ingenting.
"""

import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)

XMLID = 'ai_agent_core.provider_bifrost'


def migrate(cr, version):
    if not version:
        # Färsk installation: datafilen har redan satt rätt värde.
        return

    env = api.Environment(cr, SUPERUSER_ID, {})
    record = env.ref(XMLID, raise_if_not_found=False)
    if not record:
        _logger.info(
            '18.0.2.0.89: %s finns inte i denna databas — inget att migrera.',
            XMLID)
        return

    if record.is_key_required:
        _logger.info(
            '18.0.2.0.89: %s har redan is_key_required=True.', XMLID)
        return

    record.is_key_required = True
    _logger.info(
        '18.0.2.0.89: %s fick is_key_required=True (var False — '
        'nyckelfältet var dolt i formuläret). api_key är orört.',
        XMLID)
