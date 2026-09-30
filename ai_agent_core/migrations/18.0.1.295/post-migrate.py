# -*- coding: utf-8 -*-
"""Migrate to 18.0.1.295: rätta skillens versionspåstående.

(office-document-agent 7.3)

Skillen ligger i ett `noupdate="1"`-block och uppdateras därför inte av
data-XML. Vid verifieringen av "ny version"-flödet visade det sig att
skillens påstående var FELAKTIGT:

  "Ny version — samma namn i samma mapp; document_version räknas upp av
   modellen"

Två hinder:
  1. `dms.file._check_name` förbjuder två filer med samma namn i samma
     mapp ("A file with the same name already exists in this directory").
  2. Modulen `document_version` är trasig i Odoo 18 — `_check_name`
     anropar `name_get()`, som togs bort i Odoo 17.0. Den kraschar vid
     varje `dms.file`-create och kan inte användas.

Denna migrering ersätter avsnitt 9 i recipe_text med de två vägar som
faktiskt fungerar. Idempotent: kör om utan effekt.
"""

import logging

_logger = logging.getLogger(__name__)

GAMMALT = """# Ny version — samma namn i samma mapp; document_version räknas upp av modellen"""

NYTT = """\u26a0 **Ny version av ett DMS-dokument:** `dms` tillåter INTE två filer med
samma namn i samma mapp (`A file with the same name already exists in this
directory`). Modulen `document_version` är dessutom trasig i Odoo 18
(anropar `name_get`, som togs bort i 17.0) — den kan inte användas.

Två fungerande vägar:

1. **Ny post med versionssuffix** — `resultat_v2.pptx`. Enkelt, spårbart,
   ingen konflikt.
2. **Skriv över innehållet på befintlig post** — samma post, nytt innehåll.
   \u26a0 Detta förstör föregående version. Fråga alltid användaren först, och
   kontrollera `is_locked` (\u00a72) så du inte skriver över någon annans arbete."""


def migrate(cr, version):
    _logger.info("Running migration 18.0.1.295: rätta skillens versionspåstående")

    from odoo import api, SUPERUSER_ID

    env = api.Environment(cr, SUPERUSER_ID, {})
    skill = env['ai.skill'].search([('name', '=', 'office-dokument')], limit=1)
    if not skill:
        _logger.info("Skillen saknas — inget att göra")
        return

    recipe = skill.recipe_text or ''
    if GAMMALT not in recipe:
        _logger.info("Påståendet finns inte kvar (redan rättat) — inget att göra")
        return

    skill.recipe_text = recipe.replace(GAMMALT, NYTT)
    _logger.info("Rättade skillens avsnitt 9 (%d tecken)", len(skill.recipe_text))
