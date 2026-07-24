# Design: Systemtoken Pricing & Billing

## Context

Dagens `BudgetTracker` (PAPER-004) är ett enkelt hard-stop-lager — den räknar USD-kostnad i minnet och kastar `BudgetExhaustedError`. Men Vertel behöver ett kommersiellt prissättningslager ovanpå: en syntetisk token-enhet ("systemtokens") som är kundens vy av AI-förbrukning, med inbakad marginal, per-quest tak, och integration mot fakturering.

Det här design-dokumentet fångar systemtoken-ekonomin från provider-rapportering till kundfaktura.

---

## Core Concept: Systemtoken = Konsumtionshastighet

```
┌──────────────────────────────────────────────────────────────────────────┐
│                     SYSTEMTOKEN SOM HASTIGHETSMÄTARE                      │
│                                                                          │
│  Kunden köper: 1M systemtokens = 75 SEK (på förskott)                     │
│                                                                          │
│  ┌────────────────────────────────────────────────────────────────────┐  │
│  │  HUR FORT TICKAR METERN?                                         │  │
│  │                                                                    │  │
│  │  DeepSeek:    █░░░░░░░░░░░░░░░░░░░  1× hastighet                  │  │
│  │  GPT-4o-mini: ████░░░░░░░░░░░░░░░░  2×                            │  │
│  │  GPT-4o:      ██████████░░░░░░░░░░  5×                            │  │
│  │  Claude:      ████████████░░░░░░░░  6×                            │  │
│  │                                                                    │  │
│  │  Samma fråga (1000 "verkliga" tokens):                             │  │
│  │    DeepSeek → 1 000 systemtokens  (75 SEK räcker till 1M frågor)  │  │
│  │    Claude   → 6 000 systemtokens  (75 SEK räcker till 167K frågor)│  │
│  └────────────────────────────────────────────────────────────────────┘  │
│                                                                          │
│  KUNDENS VAL:                                                           │
│  "Jag ska batcha 10 000 fakturor"  → DeepSeek, rutinjobb                │
│  "Jag ska tolka ett avtal"          → Claude, här behövs precision       │
│  "GDPR-känsliga uppgifter"          → GDPR-modell, dyrare men compliant  │
└──────────────────────────────────────────────────────────────────────────┘
```

Systemtoken är INTE en valuta. Den är en **konsumtionshastighet** där dyra modeller tickar fortare. Kunden gör en avvägning: kapacitet vs kvalitet vs integritet.

---

## Data Flow: Real Tokens → Systemtokens

Vi bygger vidare på den befintliga infrastrukturen i `ai_agent`: `ai.quest.session.line` har redan `token` (verkliga) och `token_sys` (system). Idag är `token_sys = token × 12` — hårdkodat. Vi byter ut `×12` mot en modell-specifik multiplikator från `ai.model`.

```
┌──────────────────────────────────────────────────────────────────────────┐
│                     SYSTEMTOKEN-FLÖDET (med befintlig infrastruktur)      │
│                                                                          │
│  ┌──────────────────────────────────────────────────────────────────┐    │
│  │  PROVIDERS (Bifrost + Direct)                                    │    │
│  │                                                                  │    │
│  │  Rapporterar per anrop:                                          │    │
│  │    { model, input_tokens, output_tokens, model_real }            │    │
│  │                                                                  │    │
│  │  Bifrost: usage_metadata i API-svar                              │    │
│  │  Direct: usage_metadata från provider-svar                       │    │
│  └───────────────────────────┬──────────────────────────────────────┘    │
│                              │                                           │
│                              ▼                                           │
│  ┌──────────────────────────────────────────────────────────────────┐    │
│  │  ai.quest.session.line.new_line()  ← redan implementerat!        │    │
│  │                                                                  │    │
│  │  1. Tar emot usage_metadata från AIMessage                       │    │
│  │  2. Delar upp i token-typer (input, output, audio, cache...)     │    │
│  │  3. Skapar en line per token-typ med:                            │    │
│  │       token          = verkliga tokens                           │    │
│  │       model_real     = modellens riktiga namn                    │    │
│  │       sys_multiplier = ai.model.sys_multiplier  ← NYTT           │    │
│  │       token_sys      = token × sys_multiplier   ← compute       │    │
│  └───────────────────────────┬──────────────────────────────────────┘    │
│                              │                                           │
│                              ▼                                           │
│  ┌──────────────────────────────────────────────────────────────────┐    │
│  │  REDOVISNING (redan implementerat!)                              │    │
│  │                                                                  │    │
│  │  ai.quest.session_line_count = SUM(token_sys) för månaden        │    │
│  │    ↑ compute_session_line_count() filtrerar på innevarande månad  │    │
│  │                                                                  │    │
│  │  ai.quest.agent_count = antal distinkta agenter                  │    │
│  │    ↑ compute_agent_count()                                       │    │
│  │                                                                  │    │
│  │  Webb-UI per session  ← syns för kund                            │    │
│  │  Webb-UI per quest    ← session_line_count (månadens förbrukning) │    │
│  └──────────────────────────────────────────────────────────────────┘    │
│                                                                          │
│  DET SOM ÄNDRAS:                                                         │
│    token_sys = token × 12          →  token_sys = token × sys_multiplier │
│    (hårdkodat)                         (från ai.model, admin-styrt)      │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## ai.model — Enkel multiplikator

Istället för separata input/output-priser använder vi en **enkel multiplikator** per modell. Detta är tillräckligt eftersom Vertels marginal är inbakad och kunden bara ser "hastigheten". Output/input-skillnaden hanteras genom att output-tokens redan räknas separat i session lines.

```python
class AIModel(models.Model):
    _inherit = 'ai.model'

    sys_multiplier = fields.Float(
        'Systemtoken-multiplikator',
        default=1.0,
        help="Hur många systemtokens 1 verklig token kostar."
             " 1.0 = DeepSeek, 5.0 = GPT-4o, 6.0 = Claude."
             " Inkluderar Vertels marginal."
    )

    # Underliggande kostnad (för admin-insyn, ej kundsynlig)
    provider_cost_1M = fields.Float('Provider $/1M tokens',
        help="Vad providern faktiskt tar betalt. För admin-insyn.")
```

**Varför en multiplikator istället för input/output-priser?**
- `token_sys = token × sys_multiplier` är redan mönstret i koden
- Input och output är separata session lines — varje line får rätt multiplikator
- Admin sätter ett tal, koden är trivial
- Mindre fält att underhålla

**Admin-vyn** visar multiplikator + provider-kostnad, så Vertel ser marginalen.

**Kundvyn** visar ENDAST multiplikatorn som text: "Claude: 6× systemtokens".

**Exempel:**
```
┌──────────────┬────────────────┬───────────────────┐
│ Modell        │ sys_multiplier │ Provider $/1M     │
├──────────────┼────────────────┼───────────────────┤
│ deepseek-v4   │ 1.0            │ 0.14              │
│ gpt-4o-mini   │ 1.5            │ 0.15              │
│ gpt-4o        │ 5.0            │ 2.50              │
│ claude-s4     │ 6.0            │ 3.00              │
│ embedding     │ 0.1            │ 0.02              │
└──────────────┴────────────────┴───────────────────┘
```

---

## ai.quest.session.line — Utökning

```python
class AIQuestSessionLine(models.Model):
    _inherit = 'ai.quest.session.line'

    # Multiplikatorn som gällde när denna line skapades
    sys_multiplier = fields.Float(
        'Systemtoken-multiplikator',
        help="Fryses vid skapande — värdet från ai.model just då."
    )

    # token_sys finns redan men blir compute istället för hårdkodad ×12
    token_sys = fields.Integer(
        compute='_compute_token_sys',
        store=True,
        help="token × sys_multiplier"
    )

    @api.depends('token', 'sys_multiplier')
    def _compute_token_sys(self):
        for line in self:
            line.token_sys = int(line.token * line.sys_multiplier)

    def new_line(self, session, aimessage, agent=None, **kwargs):
        # ... befintlig logik ...
        # NYTT: hämta sys_multiplier från modellen
        model_real = aimessage.response_metadata.get('model_name', '')
        ai_model = self.env['ai.model'].search(
            [('name', '=', model_real)], limit=1
        )
        record['sys_multiplier'] = ai_model.sys_multiplier if ai_model else 1.0
        # token_sys beräknas automatiskt via compute
```

**Varför spara multiplikatorn på linen?** (Beslut D8)
- Revision: man ser exakt vilken prisnivå som gällde
- Om admin ändrar pris: gamla linjer påverkas inte (korrekt — de debiterades till gammalt pris)
- `token_sys` är compute → kan räknas om vid behov, men normalt read from store

## ai.quest — Tak och överskridande

```python
class AIQuest(models.Model):
    _inherit = 'ai.quest'

    # Tak: antal påbörjade M-tokens per månad. 0 = inget tak.
    monthly_cap_mtokens = fields.Integer(
        'Månadstak (M systemtokens)',
        default=0,
        help="0 = obegränsat. Taket anges i påbörjade miljoner systemtokens."
    )

    # session_line_count finns redan — SUM(token_sys) för innevarande månad
    # Den räknar nu automatiskt med modell-specifik multiplikator!

    # Status för tak
    cap_warning_sent = fields.Boolean('Varning skickad')
    cap_exhausted = fields.Boolean('Tak överskridet')

    # Antal påbörjade M-tokens (för faktureringsunderlag)
    started_mtokens = fields.Integer(
        compute='_compute_started_mtokens',
        help="ceil(session_line_count / 1 000 000)"
    )

    @api.depends('session_line_count')
    def _compute_started_mtokens(self):
        for quest in self:
            import math
            quest.started_mtokens = math.ceil(
                quest.session_line_count / 1_000_000
            ) if quest.session_line_count else 0
```

### Tak-beteende

```
┌──────────────────────────────────────────────────────────────────────┐
│  TAK-POLICY (per quest)                                             │
│                                                                      │
│  monthly_cap_mtokens = 0                                            │
│    → Inget tak. Obegränsad förbrukning. Faktureras i efterskott.     │
│                                                                      │
│  monthly_cap_mtokens = 5 (5M systemtokens/månad)                    │
│    → Vid 80% (4M): skicka varning till kunden (discuss/email)       │
│    → Vid 100% (5M): stoppa agenten, notifiera kund                  │
│    → Kunden kan höja taket → agenten återaktiveras                   │
│    → Överskridande faktureras på nästa faktura                       │
│                                                                      │
│  INGET automatiskt byte av modell. Kunden styr själv.               │
│                                                                      │
│  "Påbörjad miljon" betyder:                                         │
│    använt 0.3M → räknas som 1M mot taket                            │
│    använt 1.0M → räknas som 1M                                      │
│    använt 1.1M → räknas som 2M                                      │
│  På fakturan: 75 kr × antal påbörjade M-tokens.                     │
└──────────────────────────────────────────────────────────────────────┘
```

---

## Tool/Memory/Graph/RAG — Prissättning

Verktyg ska inte vara gratis. De drar från samma systemtoken-pott som LLM-anropen.

```python
class AITool(models.Model):
    _inherit = 'ai.tool'

    sys_cost_per_call = fields.Float(
        'Systemtokens per anrop',
        default=0,
        help="Fast kostnad i systemtokens för varje tool-anrop."
             " 0 = ingen extra kostnad utöver LLM-tokens."
    )

# Defaultvärden (överskridbara per quest):
#   graph_query         → 500 systemtokens / anrop
#   search_read         → 100 systemtokens / anrop
#   embedding           → 10 systemtokens / 1000 tecken
#   RAG/ladda upp fil   → 50 systemtokens / 1000 tecken
#   memory extraction   → 100 systemtokens / extraction
#   inkommande mail     → 200 systemtokens / mail tillagd i graph
```

Dessa dras **utöver** LLM-tokens. En fråga som triggar en tool-kedja:

```
Fråga till Claude:           800 × 5 /1000 = 4 000 sys.tok
graph_query tool:            500 sys.tok (fast)
search_read tool:            100 sys.tok (fast)
Claude sammanfattar:       1 200 × 8 /1000 = 9 600 sys.tok
─────────────────────────────────────────────────────────
Totalt:                                    14 200 systemtokens
```

---

## Larmkedja

```
┌──────────────────────────────────────────────────────────────────────────┐
│                        LARMSCENARIER                                     │
│                                                                          │
│  SCENARIO 1: Bifrost-nyckeln slut hos provider                          │
│  ───────────────────────────────────────                                 │
│  Bifrost märker att OpenRouter/OpenAI nekar (429/402)                    │
│       │                                                                  │
│       ▼                                                                  │
│  Bifrost → Zabbix trap                                                   │
│       │                                                                  │
│       ▼                                                                  │
│  Zabbix larmar enligt plan                                               │
│                                                                          │
│                                                                          │
│  SCENARIO 2: Kunds systemtokens överskrider tak                         │
│  ────────────────────────────────────────                                │
│  Odoo: ai.quest.systemtokens_used ≥ monthly_cap_mtokens × 1M            │
│       │                                                                  │
│       ▼                                                                  │
│  ai_agent_zabbix modul → Zabbix trap (via zabbix_sender eller API)      │
│       │                                                                  │
│       ▼                                                                  │
│  Zabbix larmar enligt plan                                               │
│       │                                                                  │
│       ▼                                                                  │
│  Odoo notifierar kund direkt (discuss/email varning)                     │
│                                                                          │
│                                                                          │
│  SCENARIO 3: Kund med egen direkt-nyckel                                │
│  ────────────────────────────────────                                    │
│  Samma som scenario 2 — DirectProvider rapporterar tokens till Odoo     │
│  Odoo sköter konvertering + tak-kontroll                                 │
│  Vid 429 från providern → Odoo→Zabbix + kundvarning                     │
│                                                                          │
│  Kundens vy är identisk oavsett om nyckeln går via Bifrost eller direkt. │
└──────────────────────────────────────────────────────────────────────────┘
```

### ai_agent_zabbix — separat modul

```
ai_agent_zabbix/
├── __manifest__.py      (depends: ai_agent_core, zabbix_base)
├── models/
│   └── ai_zabbix.py     (Zabbix trap-sändning)
├── data/
│   └── zabbix_items.xml (Zabbix items för Odoo-metriker)
└── README.md

Gränssnitt:
  - ai.agent: zabbix_host, zabbix_item_key
  - ai.quest: zabbix_item_key (för tak-överskridning)
  - Metod: _send_zabbix_trap(key, value, host)
  - Triggers: på write() av systemtokens_used (över 80% → warning trap,
    över 100% → critical trap)
```

---

## Kundens vy

### Web-chat (per session)

```
┌──────────────────────────────────────────────────────────────────────┐
│  AI CHAT — Bokföringsanalys                                   [🌗]   │
│                                                                      │
│  ┌──────────────────────────────────────────────────────────────┐    │
│  │  Modell: [Claude 4 ▾]  (5-8 systemtokens / 1K tokens)        │    │
│  │  Månadstak: 5M  |  Förbrukat: 1.2M (24%)  |  Kvar: 3.8M     │    │
│  │  ⚠️ Tak nås ~23 mars med nuvarande förbrukning               │    │
│  └──────────────────────────────────────────────────────────────┘    │
│                                                                      │
│  [Chat-meddelanden här...]                                           │
│                                                                      │
│  ┌──────────────────────────────────────────────────────────────┐    │
│  │  [Skriv meddelande...]                        [📎]  [Skicka] │    │
│  │  Session: 14 200 systemtokens (≈ 1.07 SEK)                   │    │
│  └──────────────────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────────────────┘
```

### Quest-översikt (Odoo backend)

```
ai.quest "Bokföringsanalys"
├── Månadstak: 5M systemtokens
├── Förbrukat denna månad: 1.2M (24%)
├── Smartknapp: [Senaste månaden: 3.8M] → öppnar detaljvy
├── Sessioner denna månad: 47
└── Genomsnitt/session: 25 500 systemtokens
```

---

## Faktureringsgränssnitt

**Denna Odoo-instans fakturerar INTE.** Fakturering sker från en separat Odoo-instans (Vertels ekonomisystem). Denna instans exponerar endast förbrukningsdata som fakturerings-odon läser in.

```
┌──────────────────────────────────────────────────────────────────────────┐
│                     FAKTURERINGSGRÄNSSNITT                               │
│                                                                          │
│  ┌──────────────────────┐         ┌──────────────────────────────┐      │
│  │  Kundens Odoo         │         │  Vertels ekonomisystem       │      │
│  │  (denna instans)      │         │  (annan Odoo)               │      │
│  │                       │         │                              │      │
│  │  ai.quest             │  API/   │  Läser förbrukning           │      │
│  │  ├── monthly_cap      │  XMLRPC │  Skapar faktura             │      │
│  │  ├── systemtokens_    │ ──────→ │  Hanterar betalning          │      │
│  │  │   used_this_month  │         │                              │      │
│  │  ├── user_count       │         │  Prislista (Vertels sida):   │      │
│  │  └── quest_count      │         │    125 kr / användare        │      │
│  │                       │         │     75 kr / ai.quest         │      │
│  │  Rapporterar:         │         │     75 kr / påbörjad 1M      │      │
│  │  • Förbrukade M-tok   │         │                              │      │
│  │  • Antal användare    │         │                              │      │
│  │  • Antal quests       │         │                              │      │
│  └──────────────────────┘         └──────────────────────────────┘      │
│                                                                          │
│  PERIOD:                                                                  │
│    Kalendermånad. "Påbörjad M-token" = round_up(förbrukat / 1 000 000)  │
│    Ex: 1.2M förbrukat → 2 påbörjade M-tokens                             │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## Beslut (decisions)

### D1 — Systemtoken räknas i Odoo, inte i Bifrost

**Beslut:** Bifrost och Direct providers rapporterar **verkliga tokens** (input, output, model) till Odoo. Odoo konverterar till systemtokens med admin-styrd prissättning på `ai.model`.

**Varför:** Marginal-logik och prissättning är Vertels affärslogik — den ska inte ligga i infrastruktur-lagret (Bifrost). Admin-gränssnittet för att justera priser är i Odoo. Kundvyn är i Odoo. Allt som är "business" är i Odoo.

### D2 — Systemtoken = konsumtionshastighet, inte valuta

**Beslut:** Systemtoken är en abstrakt enhet där 1M tokens = 75 SEK. Dyra modeller har högre systemtokens-per-1K-real-tokens. Kunden ser det som "hastighet" — Claude tickar 6× snabbare än DeepSeek.

**Varför:** Detta gör prissättningen begriplig för kunden utan att exponera Vertels marginaler. Kunden fattar "jag har 5M tokens — ska jag bränna dem på Claude eller spara med DeepSeek?"

### D3 — Inget automatiskt modell-byte

**Beslut:** När ett tak nås stoppas agenten med en varning. Inget automatiskt fall-back till billigare modell.

**Varför:** Kunden ska ha kontroll. Att automatiskt byta modell (t.ex. Claude→DeepSeek) kan ge sämre svar utan att kunden förstår varför. Det är bättre att stoppa och låta kunden ta ett medvetet beslut.

### D4 — Tak anges i "påbörjade M-tokens", default 0 (inget tak)

**Beslut:** `monthly_cap_mtokens = 0` betyder obegränsat. Taket är per quest, angivet i hela miljoner. Förbrukat avrundas uppåt (1.2M → 2M mot taket).

**Varför:** 0 som default är minst överraskande — existerande kunder påverkas inte. "Påbörjad miljon" matchar faktureringsmodellen (75 kr/påbörjad 1M). Enkelt för kunden att förstå.

### D5 — Zabbix-integration i separat modul

**Beslut:** `ai_agent_zabbix` som egen Odoo-modul, beroende på `ai_agent_core` och `zabbix_base`.

**Varför:** Inte alla installationer har Zabbix. Clean separation. Kan utvecklas och testas oberoende.

### D6 — Kund med egen nyckel får samma behandling

**Beslut:** DirectProvider och BifrostProvider rapporterar tokens på samma sätt till Odoo. Odoo konverterar till systemtokens och tillämpar samma tak/varningar oavsett källa.

**Varför:** Kunden ska inte behöva bry sig om infrastrukturen bakom. En enhetlig vy oavsett om tokens kommer via Bifrost eller direkt.

### D7 — Tools/Memory/Graph/RAG kostar systemtokens

**Beslut:** Alla verktyg har en `sys_cost_per_call` på `ai.tool`. Default-värden sätts per tool-typ. Kostnaden dras från samma systemtoken-budget som LLM-anrop.

**Varför:** Embeddings, graph-sökningar och RAG kostar faktiska resurser (CPU, minne, ibland API-anrop via Bifrost). De ska inte vara gratis.

### D8 — Multiplikator lagras på session line (Alternativ C)

**Beslut:** `sys_multiplier` fryses på `ai.quest.session.line` vid skapande. `token_sys = token × sys_multiplier` är en stored compute.

**Varför:**
- **Revision:** man ser exakt vilken prisnivå som gällde när linen skapades
- **Prisändringar:** om admin ändrar `ai.model.sys_multiplier` påverkas inte gamla linjer — korrekt, de debiterades till gammalt pris
- **Flexibilitet:** `token_sys` är compute → kan räknas om vid behov (t.ex. om ett fel upptäcks), men normalt read from store
- **Prestanda:** stored compute undviker att SUM-mera miljontals rader med join mot `ai.model`

---

## Risker & trade-offs

| Risk | Mitigation |
|------|------------|
| **Admin sätter fel multiplikator** → kund under/överdebiteras | `ai.model` har `provider_cost_1M` för intern kontroll. Jämför intäkt (sys_multiplier × 75 SEK/1M) mot provider-kostnad.
| **Multiplikatorn ändras mitt i månaden** | Gamla session lines har redan den gamla multiplikatorn — korrekt. Nya anrop får den nya. Ingen retroaktivitet.
| **Systemtoken-uträkning blir CPU-tung** | Beräkningen är enkel multiplikation per line. Görs en gång vid skapande (compute stored).
| **Kund förstår inte systemtoken-modellen** | Förklara i UI: "Claude: 6× systemtokens". Visa hastighets-metafor.
| **Påbörjad M-token ger orättvisa** | Kunden får "växel" över månadsgränsen — en M-token som påbörjats i mars men inte förbrukats fullt räknas in i mars. April börjar på 0.
| **Bifrost-rapportering missar anrop** | Odoo räknar även tokens från `AIProvider.chat()`-svaren. Bifrost-rapportering är ett extra lager för verifikation, inte enda källan.

---

## Öppna frågor

1. **Exakt hur Bifrost rapporterar usage till Odoo?** Push (Bifrost POST:ar till Odoo) eller pull (Odoo pollar Bifrost)?
2. **Systemtoken-reset?** Per kalendermånad? Per 30-dagars rullande?
3. **Embedding-modeller?** Egna systemtoken-priser för text-embedding-3-small, text-embedding-3-large, etc.?
4. **API för fakturerings-odon?** Vilka fält behöver exponeras via XMLRPC/JSON-RPC? Räcker standard Odoo API eller behövs ett dedikerat reporting-endpoint?

---

## Nästa steg

1. Skapa `proposal.md` + `tasks.md` för denna change
2. Specificera delta-specs:
   - `specs/systemtoken-pricing/spec.md`
   - `specs/quest-budget-caps/spec.md`
   - `specs/zabbix-integration/spec.md`
3. Implementationsordning:
   - Fas 1: `ai.model` utökning + konverteringsmotor
   - Fas 2: `ai.quest` tak + varningar
   - Fas 3: Kund-vy i webb-UI + Odoo backend
   - Fas 4: `ai_agent_zabbix` modul
   - Fas 5: API för fakturerings-odoons förbrukningsdata
