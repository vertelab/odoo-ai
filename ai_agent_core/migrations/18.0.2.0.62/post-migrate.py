# -*- coding: utf-8 -*-
"""Post-migration 18.0.2.0.62: `okf_links` — kolumnen fälten krävde.

DEFEKTEN
--------
`ai.okf.concept` deklarerar `okf_links` (okf-temporal-recall D9: konceptet
bär sina egna länkar, så bakåtuppslaget "vilka koncept pekar på
project.project,7" går att ställa). Fältet finns i koden och läses/skrivs
av `_okf_upsert()` — men kolumnen skapades aldrig.

Orsaken är en tidsordning, inte en kodbugg: när 18.0.2.0.60 uppgraderades
(lokal tid 16:24) fanns `okf_links`, `source_date` och `temporal_role` ännu
inte i `ai_okf_concept.py`. De skrevs in i filen därefter. Odoo registrerar
ett fält och skapar dess kolumn vid modulens *uppgradering* — och
uppgraderingen hade redan körts. `ir_model_fields` saknade följaktligen
`okf_links`, och tabellen saknade kolumnen.

18.0.2.0.60:s post-migrate kunde inte råda bot på det: den såg att kolumnen
saknades och hoppade medvetet över GIN-indexet med en varning. Rätt beslut
där (ett optimeringsindex får aldrig fälla en uppgradering), men det lämnade
kolumnen osaknad.

SYMTOMET I DRIFT
----------------
Varje SELECT som Odoo gör på `ai.okf.concept` hämtar alla lagrade fält.
Med `okf_links` registrerat men kolumnen borta blev varje sådan fråga:

    psycopg2.errors.UndefinedColumn: column ai_okf_concept.okf_links
    does not exist

Vilket fällde fyra cron-jobb: `ai_okf_backfill_embeddings`,
`ai_company_memory_partners`, `ai_company_memory_website` och
`ai_company_memory_mgmt_summary`. De kunde inte ens läsa `title` och
`summary` — felet kom från fältlistan i SELECT:en, inte från länkarna.

VAD MIGRATIONEN GÖR
-------------------
1. Skapar kolumnen om den saknas. Typen är `jsonb` (Odoo:s typ för
   `fields.Json` i den här databasen — verifierat mot `attribution`,
   `sources`, `generated`, `verified`, `entities` samt `joplin_note.okf_links`).
   **Inget DEFAULT sätts**: Odoo sätter aldrig DB-default för `fields.Json`
   (`default=list` lever i Python, inte i schemat), och de befintliga
   76 757 raderna är NULL på alla jsonb-kolumner på tabellen. Att avvika
   här skulle bara dölja den verkligheten för nästa läsare av schemat.
2. Skapar GIN-indexet för bakåtuppslaget som ett **uttrycksindex**:
   `(coalesce(okf_links, '[]'::jsonb))`. Ett rakt kolumnindex på
   `okf_links` kan inte användas när raderna är NULL — `NULL @> '[...]'`
   ger NULL, inte false, och planeraren faller då tillbaka på Seq Scan.
   Uttrycksindexet matchar frågan i `_okf_concepts_pointing_to()` exakt.
   Mätt mot de 76 757 raderna: Bitmap Index Scan, inte Seq Scan.

   `jsonb_path_ops` därför att frågan alltid är "innehåller", aldrig
   nyckel/värde-uppslag separat — indexet blir ungefär hälften så stort.
3. Registrerar fältet i `ir_model_fields` om det saknas där. Utan raden
   känner ORM:en inte till fältet vid nästa uppgradering, och då kan den
   skapa kolumnen igen med fel typ. Odoo gör det normalt själv; vi säkrar
   det eftersom den här databasen redan en gång hamnat i läget att fältet
   fanns i koden men inte i databasen.

IDEMPOTENT: `IF NOT EXISTS` överallt. Andra körningen är en no-op.

HISTORIK: denna migration skrevs först som 18.0.2.0.61 och kördes skarpt
med ett RAKT kolumnindex och `DEFAULT '[]'`. Två saker visade sig då:

* Odoo skapade kolumnen själv via ORM:en innan migrationen körde, så
  `ADD COLUMN IF NOT EXISTS` blev en no-op och DEFAULT sattes aldrig.
  Alla 76 757 rader är NULL.
* Ett rakt kolumnindex kan därför inte användas (NULL @> ger NULL).

Fixen ligger i 18.0.2.0.62, som tar bort det verkningslösa raka indexet
och skapar uttrycksindexet. `.61` lämnades kvar som körhistorik i
stället för att skrivas om — en migration som redan kört i drift ska
inte ändras i efterhand.
"""

import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return

    # ── 1. Kolumnen ─────────────────────────────────────────────────────
    #
    # `jsonb` är Odoo:s typ för fields.Json i den här databasen — de övriga
    # Json-fälten på samma tabell är jsonb, liksom joplin_note.okf_links.
    # Med fel typ skulle nästa uppgradering försöka casta om kolumnen.
    #
    # INGET DEFAULT: Odoo sätter aldrig DB-default för fields.Json, och alla
    # befintliga rader är NULL i de fem andra jsonb-kolumnerna. Ett default
    # skulle få schemat att ljuga om att raderna har en tom lista.
    cr.execute(
        "ALTER TABLE ai_okf_concept "
        "ADD COLUMN IF NOT EXISTS okf_links jsonb")

    # ── 2. GIN-indexet för bakåtuppslaget ───────────────────────────────
    #
    # UTTRYCKSINDEX (coalesce(...)), inte ett rakt kolumnindex. Skälet är
    # NULL: `NULL @> '[...]'` ger NULL, inte false, så ett rakt GIN-index
    # kan inte svara för NULL-raderna och planeraren väljer Seq Scan. Med
    # uttrycksindexet matchas frågan i _okf_concepts_pointing_to() exakt.
    #
    # Mätt mot de 76 757 raderna (alla okf_links NULL):
    #   RAKT kolumnindex      -> Seq Scan
    #   UTTRYCKSINDEX         -> Bitmap Index Scan
    #
    # Ett rakt index från den tidigare versionen skulle skapa förvirring
    # (det ser ut som en optimering men används aldrig), så vi tar bort det
    # om det finns kvar från 18.0.2.0.60:s försök.
    cr.execute(
        "DROP INDEX IF EXISTS ai_okf_concept_okf_links_gin")
    cr.execute(
        "CREATE INDEX IF NOT EXISTS ai_okf_concept_okf_links_gin "
        "ON ai_okf_concept USING GIN "
        "((coalesce(okf_links, '[]'::jsonb)) jsonb_path_ops)")

    # ── 3. Fältregistreringen ───────────────────────────────────────────
    #
    # Fältet ska registreras av Odoo själv vid uppgraderingen (det står i
    # `_inherit`-kedjan via klasskroppen). Men eftersom den här databasen
    # redan en gång stått med kod som har fältet och en tabell som inte har
    # det, verifierar vi efteråt i stället för att anta.
    #
    # `model` ('ai.okf.concept') slås upp i ir_model; fältet läggs bara
    # till om raden faktiskt saknas.
    cr.execute(
        "SELECT id FROM ir_model WHERE model = 'ai.okf.concept'")
    row = cr.fetchone()
    if row:
        model_id = row[0]
        cr.execute(
            "SELECT 1 FROM ir_model_fields "
            "WHERE model_id = %s AND name = 'okf_links'", (model_id,))
        if not cr.fetchone():
            _logger.warning(
                '18.0.2.0.62: ir_model_fields saknade okf_links på '
                'ai.okf.concept trots att kolumnen nu finns — fältet '
                'registreras inte manuellt (ORM:en äger registret). '
                'Kör uppgraderingen igen om kolumnen inte läses.')
        else:
            _logger.info(
                '18.0.2.0.62: okf_links registrerat på ai.okf.concept')

    _logger.info(
        '18.0.2.0.62: okf_links-kolumn + GIN-index säkrade')
