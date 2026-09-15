# -*- coding: utf-8 -*-
"""Markera vilken provider som kan embedda — och vilken modell som fungerar.

Varför den här filen finns
--------------------------
Fas 3 byggde efterfyllnaden av vektorer. Den anropade

    provider = env['ai.provider'].search([('active', '=', True)], limit=1)

Ett OBUNDET val. I drift landade det på Anthropic (id 2), vars /embeddings
svarar HTTP 404:

    WARNING ai_provider: Embedding-fel (provider=Anthropic,
      modell=text-embedding-3-small): HTTP 404 ... "Not found"

Följden: `embedding_state` stod kvar på 'pending' för 110 av 110 koncept,
den semantiska signalen var tyst död, och BM25 såg ut att fungera. Det är
sjunde gången samma mönster (design.md §15–§17): SKRIVSIDAN BYGGDES,
LÄSSIDAN ANTOGS.

Under utredningen (2026-09-14) mättes gatewayen mot verkligheten:

  POST /v1/embeddings  model=text-embedding-3-small  → 000/401  (avvisas)
  POST /v1/embeddings  model=intfloat/multilingual-e5-large → 200, dim=1024
  POST /v1/embeddings  model=mistral-embed                 → 200, dim=1024
  POST /v1/embeddings  model=embed-multilingual-v3.0       → 200, dim=1024

Alla tre ger dim=1024, men de separerar OLIKA BRA. Mätt på samma texter
(cosine, enhetsvektorer, tre repriser — stabila till fjärde decimalen):

  modell                      samma ämne      olika ämne      slutsats
  intfloat/multilingual-e5-large   0.8510         0.7837      oanvändbar
      (kaffe 0.8199 > kvantmekanik 0.7837 — INVERTERAD rangordning)
  mistral-embed                    0.7407         0.7509      oanvändbar
      (olika ämne hamnar ÖVER samma ämne)
  embed-multilingual-v3.0          0.7102         0.4302      ANVÄNDBAR
      (tydligt gap; rangordnar rätt)

Rangordningstest mot fem verkliga koncept-texter: 'embed-multilingual-v3.0'
placerade rätt dokument först i 4 av 5 fall (den femte var ett dött lopp
0.5341 mot 0.5200 — båda "företag med kontaktuppgifter").

VALET AV MODELL ÄR ALLTSÅ EN MÄTNING, INTE EN ÅSIKT. Att välja
'multilingual-e5-large' för att den är svensk och populär hade gett en
vektorkolumn fylld med vektorer som inte rangordnar — sämre än tomma
vektorer, eftersom felet då är osynligt (§16: ett TAL antogs betyda något).

OBS om prefix: e5-familjen vill ha 'query: '/'passage: '. Det ändrar gapet
(+0.0152 utan prefix, +0.0255 med) men RÄDDAR INTE en inverterad
rangordning. Prefixet är därför inte lösningen på e5-problemet.

Härdning (lärdom från migrations/1.11, som svalde sitt ALTER TABLE):
- Ingen bred try/except. Ett fel ska stoppa och synas.
- Idempotent: sätter bara värden som saknas eller är fel.
- Räknar och loggar vad den gjorde, så inställningen går att verifiera.

VIKTIGT — post-migrate kör FÖRE ORM:en skapar nya kolumner
-----------------------------------------------------------
Första körningen av den här filen kraschade med

    ERROR: column "can_embed" does not exist

Det är inte ett fel i migrationen — det är ordningen. Odoo kör
post-migrate mellan 'ladda modulens filer' och 'uppdatera databasschemat'.
Fälten `can_embed`/`embedding_model`/`embedding_dim` som deklareras i
models/ai_provider.py existerar därför ÄNNU INTE när denna kod kör.

Exakt samma sak hände migrations/1.11: den gjorde ALTER TABLE på en tabell
ORM:en ännu inte skapat. Skillnaden är att 1.11 SVALDE felet med
`_logger.warning('non-fatal')`, och konsekvensen levde i månader: kolumnen
`search_vector` saknades helt (se Fas 11). Den här filen kraschade i stället
— vilket är precis vad arbetsordningen kräver. Ett fel som syns är billigt.

Lösningen är att skapa kolumnerna SJÄLV om de saknas. Då fungerar filen
både vid uppgradering (kolumnerna finns) och vid en färsk installation där
post-migrate råkar köra först.

RÖR INTE api_key. Nyckeln är en hemlighet och fylls i av drift/UI (eller
redan via ir.config_parameter 'bifrost.admin_api_key'). Denna migration
sätter bara KAPABILITET (can_embed, modell, dimension).
"""

import logging

_logger = logging.getLogger(__name__)

# Verifierat svarande modell (HTTP 200, dim=1024) med tydlig separation.
VERIFIED_MODEL = 'embed-multilingual-v3.0'
VERIFIED_DIM = 1024


def _ensure_columns(cr):
    """Skapa kolumnerna om ORM:en ännu inte hunnit.

    Post-migrate kör mellan filnladdning och schema-synk, så ett fält som
    är nytt i den här versionen finns inte i databasen ännu. Vi skapar det
    med SAMMA definition som ORM:en skulle gjort, så att den efterföljande
    schemasynken bara ser ett no-op.

    Idempotent: ADD COLUMN IF NOT EXISTS.
    """
    cr.execute("""
        ALTER TABLE ai_provider
            ADD COLUMN IF NOT EXISTS can_embed boolean,
            ADD COLUMN IF NOT EXISTS embedding_model varchar,
            ADD COLUMN IF NOT EXISTS embedding_dim integer
    """)
    # ORM:ens default är false, men en ADD COLUMN sätter NULL på befintliga
    # rader. Utan detta hade `can_embed` varit NULL (falsy, men inte false)
    # och ett framtida `WHERE can_embed = false` hade missat alla rader.
    cr.execute("UPDATE ai_provider SET can_embed = false WHERE can_embed IS NULL")


def migrate(cr, version):
    from odoo import api, SUPERUSER_ID

    env = api.Environment(cr, SUPERUSER_ID, {})

    _ensure_columns(cr)

    # 1. Nollställ: bara EN provider får vara embedding-källa. Att lämna
    #    flera markerade hade gjort `_embedding_provider()`-valet godtyckligt
    #    igen — samma bugg i ny skepnad.
    cr.execute("UPDATE ai_provider SET can_embed = false WHERE can_embed = true")

    # 2. Välj bifrost-providern (den enda gatewayen).
    cr.execute("""
        SELECT id, name FROM ai_provider
        WHERE provider_type = 'bifrost' AND active = true
        ORDER BY id LIMIT 1
    """)
    row = cr.fetchone()
    if not row:
        _logger.warning(
            'Embedding-provider: ingen aktiv bifrost-provider hittad — '
            'lämnar can_embed orört. Vektorer uteblir tills en provider '
            'markerats (embeddings är då ärligt frånvarande, inte tysta).')
        return

    provider_id, provider_name = row

    cr.execute("""
        UPDATE ai_provider
        SET can_embed = true,
            embedding_model = %s,
            embedding_dim = %s
        WHERE id = %s
    """, (VERIFIED_MODEL, VERIFIED_DIM, provider_id))

    _logger.info(
        'Embedding-provider satt: %s (id=%s), modell=%s, dim=%s — '
        'verifierad mot gatewayen 2026-09-14.',
        provider_name, provider_id, VERIFIED_MODEL, VERIFIED_DIM)

    # 3. Verifiera att valet går att LÄSA tillbaka — inte bara att UPDATE
    #    körde. En tyst no-op är precis vad migrations/1.11 gjorde.
    cr.execute("""
        SELECT can_embed, embedding_model, embedding_dim
        FROM ai_provider WHERE id = %s
    """, (provider_id,))
    can_embed, model, dim = cr.fetchone()
    if not can_embed or model != VERIFIED_MODEL or dim != VERIFIED_DIM:
        raise RuntimeError(
            'Embedding-provider kunde inte verifieras efter UPDATE: '
            'can_embed=%r model=%r dim=%r' % (can_embed, model, dim))

    _logger.info(
        'Verifierat: can_embed=%s modell=%s dim=%s. '
        'Providers som INTE kan embedda är nu explicit omarkerade.',
        can_embed, model, dim)
