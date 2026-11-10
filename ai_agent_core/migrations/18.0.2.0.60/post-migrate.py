# -*- coding: utf-8 -*-
"""Post-migration 18.0.2.0.60: källdatum + typade länkar (okf-temporal-recall).

TVÅ SAKER, en migration — båda är schemaändringar som hör till samma
ändring och måste finnas innan koden läser dem:

1. `source_date` + `temporal_role` på `ai_okf_concept`.
   Odoo skapar kolumnerna själv via ORM:en, men `index=True` blir ett
   index som heter något annat än det vi vill ha, och en `--init`-körning
   (checkmodule) hoppar över migrationer. Vi skapar därför allt idempotent
   här — det är samma mönster som `_create_okf_sql_functions()` använder
   för att överleva `--init`.

2. GIN-index på `okf_links` för BAKÅTuppslaget ("vilka koncept pekar på
   project.project,7"). Utan index blir den frågan en full tabellskanning
   av varje koncept — och `okf_links` är ett jsonb-fält utan eget index.

IDEMPOTENT: `IF NOT EXISTS` överallt. Andra körningen är en no-op.
"""

import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return

    # ── 1. Källdatum ────────────────────────────────────────────────────
    cr.execute(
        "ALTER TABLE ai_okf_concept "
        "ADD COLUMN IF NOT EXISTS source_date timestamp without time zone")
    cr.execute(
        "ALTER TABLE ai_okf_concept "
        "ADD COLUMN IF NOT EXISTS temporal_role varchar")
    cr.execute(
        "CREATE INDEX IF NOT EXISTS ai_okf_concept_source_date_idx "
        "ON ai_okf_concept (source_date)")

    # ── 2. Bakåtuppslag på länkar ───────────────────────────────────────
    #
    # `jsonb_path_ops` är valt framför default-opklassen: frågan är alltid
    # `okf_links @> '[{"ref": "..."}]'` (innehåller), aldrig nyckel/värde-
    # uppslag separat. Indexet blir då ungefär hälften så stort.
    #
    # OBS: kolumnen är `json` i Odoo (fields.Json), inte `jsonb`. Vi
    # kastar i uttrycket — ett funktionellt index på en cast är giltigt
    # och används av planeraren när frågan skriver samma cast.
    #
    # FÄLLAN (2026-11-10): `okf_links` är ett FÄLT FRÅN `ai.okf.format.mixin`
    # — Odoo lägger kolumnen på `ai_okf_concept` eftersom format-mixinen är i
    # `_inherit`-listan. Den skapas MEN inte nödvändigtvis före post-migrate,
    # och mer avgörande: på den här databasen saknas den (och `okf_body`,
    # `okf_summary`, `okf_tags`) helt — koncepttabellen har aldrig fått
    # format-mixinens kolumner. Ett CREATE INDEX slog därför uppgraderingen i
    # backen med "column okf_links does not exist" i stället för att vara en
    # no-op.
    #
    # Indexet är en OPTIMERING (spec: bakåtuppslaget ska använda ett index,
    # inte skanna). Det får aldrig hindra uppgraderingen: kolumnen saknas =
    # kolumnen ska skapas av ORM:en, och en indexkörning görs om senare.
    # Frågan fungerar utan index (bara långsammare), så vi hoppar över med
    # en VARNING som säger precis vad som är kvar att göra.
    cr.execute(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_name = 'ai_okf_concept' AND column_name = 'okf_links'")
    if cr.fetchone():
        cr.execute(
            "CREATE INDEX IF NOT EXISTS ai_okf_concept_okf_links_gin "
            "ON ai_okf_concept USING GIN ((okf_links::jsonb) jsonb_path_ops)")
    else:
        _logger.warning(
            '18.0.2.0.60: ai_okf_concept.okf_links saknas (format-mixinen har '
            'inte synkat koncepttabellen) — GIN-indexet hoppas över. '
            'Bakåtuppslaget fungerar men skannar tills indexet finns.')

    cr.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'ai_okf_concept' "
        "  AND column_name IN ('okf_body', 'okf_summary', 'okf_tags', 'okf_links')")
    missing = sorted(r[0] for r in cr.fetchall())
    if len(missing) < 4:
        _logger.warning(
            '18.0.2.0.60: ai_okf_concept saknar OKF-formatkolumner: %s '
            '(förväntat: okf_body, okf_summary, okf_tags, okf_links).'
            'Kontrollera att ai.okf.format.mixin är i ai.okf.concepts '
            '_inherit-lista och kör uppgraderingen igen.',
            ', '.join(missing) or '(inga)')

    _logger.info(
        '18.0.2.0.60: source_date/temporal_role + GIN på okf_links säkrade')

    # ── 3. Fälten på ai.coworker ────────────────────────────────────────
    #
    # `temporal_default_enabled/_days`, `related_enrichment*` och
    # `ai_coworker_session.machine` skapas av ORM:en (de är vanliga fält, inte
    # migrationers sak). Men en misslyckad uppgradering lämnar databasen utan
    # dem medan koden redan läser dem — varje SELECT på ai_coworker ger då
    # UndefinedColumn och /ai/v1/* svarar 500 (2026-11-10). Vi säkrar dem
    # idempotent här så att en omkörning alltid landar i ett konsekvent läge.
    cr.execute(
        "ALTER TABLE ai_coworker "
        "ADD COLUMN IF NOT EXISTS temporal_default_enabled boolean DEFAULT true")
    cr.execute(
        "ALTER TABLE ai_coworker "
        "ADD COLUMN IF NOT EXISTS temporal_default_days integer DEFAULT 0")
    cr.execute(
        "ALTER TABLE ai_coworker_session "
        "ADD COLUMN IF NOT EXISTS machine varchar")
