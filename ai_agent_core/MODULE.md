# ai_agent_core — AI Agent Core Module

## Overview

Standalone AI agent engine for Odoo. No LangChain dependency. Provides:

- **Agent Loop**: Buzz-inspired while-loop with Bifrost provider
- **Web Chat**: `/ai/chat` with SSE streaming, threads, slash commands
- **Models**: ai.coworker, ai.agent, ai.skill, ai.tool, ai.identity, ai.model, ai.provider
- **Skills system**: Reusable competencies with recipes
- **Identity system**: Agent personality (SOUL.md)

## Core Models

| Model | Description | Key Fields |
|-------|-------------|------------|
| ai.coworker | AI Medarbetare — the top-level unit | name, description, init_type, is_supervisor, identity_id |
| ai.agent | AI Agent — a specialized worker | name, bifrost_model, direct_model, provider_type, skill_ids |
| ai.coworker.agent | M2M: coworker ↔ agent assignment | coworker_id, agent_id, sequence |
| ai.skill | Reusable competency | name, recipe_text, category, trigger_keywords |
| ai.identity | Agent personality/soul | name, system_prompt, style, scope, skill_ids |
| ai.model | AI model with capabilities | name, is_vision, has_streaming, context_window, sys_multiplier |
| ai.provider | AI provider (Bifrost, OpenAI, etc.) | name, provider_type, base_url |
| ai.tool | Reusable tool | name, description, risk_level |
| ai.memory | Coworker memory (RAG facts) | name, content, category, importance |
| ai.tag | Lightweight AI tags | name, color |

## Init Types

Coworkers can be triggered in multiple ways via `ai.coworker.init_type`:

| Type | Description | Auto-created |
|------|-------------|-------------|
| web_ui | Public web chat at /ai/chat | show_in_chat flag |
| chat | Private Discuss chat | Bot user |
| channel | Discuss team channel | Channel record |
| mail | Email ingestion | Mail alias |
| cron | Scheduled execution | ir.cron record |
| server_action | UI-triggered action | Server action |
| powerbox | Context-aware from records | model_ids binding |
| manual | Manual trigger only | — |
| openai_api | OpenAI-compatible endpoint | API key |

## Key Methods on ai.coworker

- `run(prompt, system_prompt=None)` — synchronous execution via AgentLoop
- `get_available_skills()` — union of agent skills, identity skills, coworker copies
- `powerbox(prompt, res_model, res_id)` — context-aware execution
- `action_run_scheduled()` — cron-triggered execution

## Agent Configuration

```python
agent.provider_type in ('bifrost', 'direct')
agent.bifrost_model  # e.g. 'cerebras/gpt-oss-120b'
agent.direct_model   # e.g. 'claude-sonnet-4-20250514'
agent.skill_ids      # M2M to ai.skill
```

## Session Accounting (session-audit, 18.0.1.163)

`ai.coworker.session` + `ai.coworker.session.line` är granskningsspåret för
alla AI-konversationer (web-chat `/ai/chat`, openai_api `/ai/v1`,
coworker.run(), powerbox, mail). Bokföring per meddelande:

- **User-rad** bär requestens **input-tokens** (`token_input`, `token_output=0`)
  — prompt-kostnaden för det meddelandet.
- **Assistant-rad** bär **output-tokens** (`token_output`, `token_input=0`)
  samt `debug_info` (resonemang), `source_urls` och `tool_calls` (JSON-lista
  `[{name, preview}]`).
- **Tool-rader** (`role='tool'`) sparar varje verktygsanrop med `tool_name`,
  preview och `sys_token_cost` som `token_input` (multiplier 1.0).
- `token_sys` per rad = `(token_input + token_output) × sys_multiplier`;
  `session.token_sys` = Σ rader ≈ `(input+output) × multiplier` (oförändrad
  totalsumma jämfört med när input+output låg på samma rad).

Verklig token-usage i streaming:

- `TokenEvent` bär nu `input_tokens`/`output_tokens`; providern fyller dem
  via `stream_options.include_usage` (OpenAI-kompatibelt, med 400-retry utan
  för strikta gateways) eller Anthropic `message_start`/`message_delta`.
- `StreamingAgentLoop.run_stream` aggregerar usage över alla rundor och
  sätter totaler på den slutgiltiga `done`-händelsen.
- `/ai/stream` vidarebefordrar dem som `input_tokens`/`output_tokens` i
  done-SSE; frontend skickar dem till `/ai/threads/<id>/respond`
  (fallback: tidigare estimat).

Status-/livscykel (`/new`-semantik):

- `POST /ai/threads/<id>/close` — stänger tråden (`status='done'`,
  `finish_reason='closed'`). Anropas av web-UI:ets "+ Ny tråd".
- `POST /ai/threads` (thread_create) stänger användarens övriga aktiva
  sessioner (`finish_reason='new_session'`) — en ny konversation markerar
  den gamla som avslutad. Idempotent.
- `GET /ai/threads/<id>` returnerar nu per-meddelande-kontext: `status`,
  `finish_reason`, `debug_info`, `tool_calls`, `model_real`, `token_input`,
  `token_output`, `token_sys`.

## Verktygsfel — felkontraktet (atgardbara-verktygsfel, 18.0.1.215)

Varje verktygsfel bär samma uppgifter, oavsett om verktyget är inbyggt
(Python i `core/tools.py`) eller data-definierat (`ai.tool` med kod från
`data/*.xml`). Kontraktet är `ToolError.to_json()`:

| Nyckel         | Betydelse                                              |
|----------------|--------------------------------------------------------|
| `error`        | Vad som gick fel, i klartext                           |
| `parameter`    | Vilket argument felet gäller (tomt om inte aktuellt)   |
| `expected`     | Förväntat format/värde — vägledningen                  |
| `actual`       | Det faktiska värdet som avvisades                      |
| `valid_fields` | Giltiga fält, när felet är ett okänt fält              |
| `retryable`    | Om ett nytt försök är meningsfullt                     |
| `tool_name`    | Vilket verktyg som felade                              |

`to_text()` ger samma innehåll som läsbar text för modellen; `to_json()`
ger strukturen för kvalitetsloopen (`ToolSequenceCorrector`). `Tool.call()`
fångar `ToolError` och bevarar vägledningen.

### Hur `retryable` sätts

`retryable=True` — anroparen kan rätta sig och försöka igen:

- saknat eller tomt argument (`_missing_argument_error`)
- fel format/värde (`_invalid_value_error`)
- okänt fält (`_unknown_field_error`)

`retryable=False` — nytt försök med samma argument hjälper inte:

- resursen finns inte (`_missing_record_error`), t.ex. `Skill #42 not found`
- okänd modell i `describe_model`
- internt fel (`_internal_error`) — ett oväntat undantag är inte
  anroparens misstag, och `expected` säger det explicit

### Tomt argument är ett SAKNAT argument

`describe_model('')` gav tidigare `"Unknown model: "` — vilket inte namnger
vad som saknas. Ett tomt argument behandlas därför som saknat och felet
säger vilket argument det gäller, förväntat format och ett exempel.

### Fel verktyg pekar på rätt verktyg

`odoo_write` avvisar HTML-/textfält (de skrivs vid skapandet). Felet namnger
de avvisade fälten och pekar på `odoo_create` i stället för att bara neka.

### Data-definierade verktyg

Verktyg vars kod ligger i `data/*.xml` (t.ex. `youtube_tools.xml`) kan inte
importera `ToolError` — de definierar en lokal `_tool_error()` som ger
exakt samma JSON-nycklar. Det håller kontraktet identiskt över
verktygstyperna.

**OBS — noupdate-fällan:** filer under `data/` med `<odoo noupdate="1">`
läses bara vid FÖRSTA installationen, och `_seed_builtin_tools()` uppdaterar
aldrig `code` för ett verktyg som redan finns. Ändringar i sådan XML når
alltså inte ett uppgraderat system. Tvinga om raden med en migration
(`migrations/<version>/post-migrate.py`) som läser koden ur samma fil —
se `migrations/18.0.1.215/` för mönstret.

**OBS — `migrations/__pycache__` kraschar uppgraderingen:** Odoo tolkar
varje katalog under `migrations/` som en versionskod och vägrar på
`__pycache__` (`Invalid version for upgrade script`). Katalogen skapas av
`python3 -m py_compile` i `migrations/` och av Odoo själv vid import.
Rensa den före varje `checkmodule`-körning:

```bash
rm -rf ai_agent_core/migrations/__pycache__
```

Den är git-ignorerad (`.gitignore:2`) så den syns inte i `git status` —
den måste letas upp på disk.

## Innehållslig verifiering (write-verify mot en källa)

`verify_write_outcome()` kan utöver "fältet är ifyllt" begära att fältets
innehåll **motsvarar källan**. Det stänger luckan som session 18204 visade:
ett `document.page` godkändes som "klart" trots att det tappade videon,
citat, modellnamn och belägg — och angav en påhittad källa.

### Avtalsformatet

```json
{
  "model_path": "model",
  "id_path": "id",
  "checks": [
    {"field": "name", "equals_path": "values.name"},
    {"field": "content", "non_empty": true,
     "source": true, "coverage": 0.6}
  ]
}
```

- `source: true` — begär jämförelse mot källan. Utan detta sker **ingen**
  innehållsjämförelse (kostnaden syns bara där den är motiverad).
- `coverage` — tröskel 0–1, standard `0.6`. Under tröskeln blir det ett
  **fel** i `requirement_errors` (inte en varning), med ett fix-förslag som
  namnger vad som saknas.

Källan är sessionens egna rader (`ai.coworker.session.line`, inkl.
`source_urls`) och byggs **bara** när något avtal begär den
(`_contract_needs_source` → `_build_verify_source`).

### Mätningen

`measure_coverage()` mäter hur stor del av källans väsentliga delar som
återfinns i innehållet. Delarna (`extract_essential_parts`) är modellnamn,
URL:er, citat, namngivna entiteter och sifferfakta — deterministiskt, utan
LLM-anrop.

Två saker krävdes för att mätningen skulle bli rättvis på verklig data:

1. **Brusfilter** — sökmotor-omdirigeringar (Bing/Google med spårningstokens),
   katalogdomäner (`tv.nu`, `allatvkanaler.se`), annonsord och korta
   fragment är inte belägg. I session 18204 var 8 av 12 URL:er
   Bing-omdirigeringar.
2. **Kvotering per typ** — de talrika entiteterna trängde annars ut
   modellnamn och URL:er, som är de mest värdefulla beläggen.

Citat paras **sekventiellt** (första citattecknet med andra, tredje med
fjärde) — en regex kan inte veta vilket citattecken som är öppning och
vilket som är stängning, och parade ihop två åtskilda citat så att texten
mellan dem blev ett falskt citat.

### Platshållare räknas som tomt

Odoo:s html-widget skriver `<p><br></p>` när ett fält saknar innehåll — 11
tecken som passerar en naiv tomhetskontroll. `non_empty` genomskådar detta
(`_is_placeholder_html`). Det var så `document.page` id 82 godkändes.

### Modellspecifika avtal

Ett generiskt avtal (`odoo_create`) kan inte veta vilket fält som bär
innehållet på en viss modell, och en check mot ett fält som inte finns
hoppas över som **varning** — en tyst lucka. `_MODEL_VERIFICATION_CONTRACTS`
lägger därför till modellens egna checks:

```python
_MODEL_VERIFICATION_CONTRACTS = {
    'document.page': {
        'checks': [
            {'field': 'name', 'equals_path': 'values.name'},
            {'field': 'content', 'non_empty': True,
             'source': True, 'coverage': 0.6},
        ],
    },
}
```

### Acceptansfall (session 18204)

| Innehåll | Täckning | Utfall |
|---|---|---|
| Det ofullständiga dokumentet (id 82) | 9 % | FAIL — namnger saknade modellnamn |
| Halvdant (hälften av beläggen) | 32 % | FAIL |
| Fullständigt | 85 % | PASS |

Tröskeln 0.6 skiljer alltså de tre fallen.

## OKF: ägare och access i urvalet (okf-owner-and-access-scoping, 18.0.1.302)

OKF:s urval (dedup + `limit`) skedde i SQL **före** access-filtret, och
access-filtret defaultade till *synlig* när uppslaget saknades. Tre läckage
stängdes i tre steg (varje steg är självständigt revertbart):

| # | Läckage | Var | Fix |
|---|---|---|---|
| 1 | `limit` konsumerades av osynliga rader | `_okf_search` + `_format_concept_block` | access in i `WHERE` före dedup och `LIMIT` |
| 2 | `DISTINCT ON` valde en osynlig version | `_okf_search` SQL | access-villkoret ligger före dedupen |
| 3 | fail-open + radnivå avkopplad | `_format_concept_block` | `vis.get(r, False)`, `([], n)` utan attribution, `_get_visible_lines` inkopplad |

### Ägaren bärs i nyckeln OCH i unikhetsvillkoret

En post kan bli **N koncept**, ett per ägare — t.ex. en kalenderhändelse med
flera deltagare. Två saker krävs, och båda behövs:

```
   nyckeln:      '<modell>,<id>,user.<uid>'      (särskiljer i sökning/dedup)
   constraintet: UNIQUE(scope, owner_company_id, owner_user_id,
                        owner_coworker_id, concept_key, version)
```

Constraintet är det som gör två ägares rader **lagliga**; nyckeln gör dem
**särskiljbara**. Utan constraintet avvisar databasen den andra ägarens
`version = 1`.

**Ägarläget sätts bara när posten har fler än en ägare.** En enägd post
(`ai.personal.memory,42`) behåller sitt nyckelformat — annars bytte varje
befintligt personligt koncept nyckel utan vinst.

`_okf_lookup_keys()` känner fyra nyckelformer:

```
   '<modell>,<id>'                      basnyckel
   '<modell>,<id>,<lang>'               språksuffix
   '<modell>,<id>,user.<uid>'           ägarsuffix
   '<modell>,<id>,user.<uid>,<lang>'    båda
```

Leden skiljs av `user.`-prefixet. Basnyckeln returneras **utöver** den givna
nyckeln, så en befintlig kedja fortsätter i stället för att en parallell
startas.

### Så tar en brygga flera ägare

```python
class CalendarEvent(models.Model):
    _inherit = ['calendar.event', 'ai.okf.mixin']

    def _okf_owner_vals_list(self):
        """Ett koncept per deltagare som är en användare."""
        return [{'owner_user_id': u.id}
                for u in self._attendee_users()]

    def _okf_concept_key(self, lang=None, owner_id=None):
        # Ägarläget läggs på av _okf_index_record när det finns fler än
        # en ägare — bryggan behöver inte göra något.
        return super()._okf_concept_key(lang=lang, owner_id=owner_id)
```

`okf_dirty` är per **post**, inte per ägare: den rensas **en gång** efter
samtliga ägare. Ett fel mitt i loopen lämnar posten dirty och hela loppet
körs om — idempotent, men slösar.

### Access-prövningen är asymmetrisk (ÖPPEN/STÄNGD/PARTIELL)

Volymen kräver det: `project.task` ~50 000 och `res.partner` ~3 300 i
`ledningssystem` (mätt 2026-10-06). En `IN`-lista över alla synliga id:n för
en användare som ser allt spränger plan-cachen. Två `search_count` ger i
stället ett av tre svar:

```
   visible == total  ->  ÖPPEN     inget villkor (source_ref LIKE '<modell>,%')
   visible == 0      ->  STÄNGD    modellen nämns inte alls
   annars            ->  PARTIELL  id-lista (mindre än modellens total)
```

Källmodellerna läses ur datan (`_okf_source_models()`), inte ur en hårdkodad
lista — en ny brygga behöver inte registrera sig.

**Fail-closed:** en modell som inte finns i `self.env`, eller där prövningen
kastar, klassas STÄNGD — aldrig ÖPPEN. `source_ref IS NULL` ingår alltid i
villkoret och faller till injektionens nät (som avför den), inte till
"synligt".

**Känd skalrisk:** klassningen hjälper bara när rättigheterna är
**heltäckande**. `project.task` klassas PARTIELL även för `user_admin`
(Odoos standard-`ir.rule` begränsar uppgifter till användarens egna), så en
användare med 50 000 synliga uppgifter får en id-lista på 50 000 element.
Om det visar sig för dyrt i drift är nästa steg ett **negativt** villkor
(`source_ref NOT IN` för de få osynliga) i stället för ett positivt.

### Testfälla: `assertLogs(level='INFO')` fungerar inte i Odoo

Odoo sätter `logging.RUNBOT = 25` och döper om det till `'INFO'`
(`netsvc.py`), så `logging.getLevelName('INFO')` returnerar **25**.
`assertLogs(level='INFO')` sätter då loggerns nivå till 25 och filtrerar
bort riktiga INFO-poster (20) — testet ser tomt ut trots att loggen skrivs.
**Använd `level=logging.INFO` (talet), aldrig strängen.**

## Kärnan är domän-ren — manifest OCH kod (18.0.1.303)

Manifest-kravet fanns redan, och överträdelsen uppstod ändå — i **koden**:
`ai_agent_core` beroende inte på `calendar`, men anropade
`env['calendar.event']` **oskyddat** i en cron. Utan kalendern installerad
kastade raden `KeyError`, och felet dolde sig bakom en `try/except` i
cronens anropare.

Ett krav som bara ser manifestet fångar inte detta. Regeln är därför:

```
   manifestet:  depends innehaller ingen domanmodul
   koden:       ingen env['<domanmodell>'] oskyddat
```

### Vad som togs bort

| Borttaget | Varför |
|---|---|
| `cron_index_calendar` | läste `calendar.event` oskyddat; ingen `ir.cron` |
| `cron_index_chats` | ingen `ir.cron` — kördes aldrig |
| `cron_daily_consolidation` | ingen `ir.cron` — kördes aldrig |
| `cron_nightly_index` | enda anroparen av de tre ovan; ingen `ir.cron` |
| `ai_calendar_event.py` | importerades aldrig — `ai_goal_id` fanns inte |
| `ai_personal_goal.action_book_calendar()` | `raise UserError("Coming soon")` |

Kalenderindexeringen flyttar till `calendar_ai`-bryggan, som äger domänen
och registrerar sig via `_okf_register_indexable('calendar.event')`.

**Fälla:** `cron_nightly_consolidation` (aktiv `ir.cron`) är
`ai.company.memory`s metod — **inte** samma som `ai.personal.memory`s
`cron_daily_consolidation`. Namnen liknar varandra; kontrollera modellen,
inte namnet.

### Guardat, inte bara borttaget

En domänmodell nås via `env.get()` eller en närvaroprövning:

```python
   if 'calendar.event' in self.env:        # explicit guard
       events = self.env['calendar.event'].search([...])

   Model = self.env.get('dms.file')        # None om modulen saknas
   if Model:
       ...
```

**Indirekt guard räcker inte.** `ai_onboard` prövade `helpdesk.ticket` men
nådde `helpdesk.team` — det håller bara så länge båda modellerna kommer
från samma modul. `core-purity`-testet fångade luckan; båda prövas nu.

### Så prövas det

`tests/test_core_domain_purity.py` härleder kärnans beroenden ur manifestet
och söker modellkoden efter `env['<domänmodell>']` där modellen inte är ett
kärnberoende. Varje träff måste ha en guard inom åtta rader.

```
   DOMAIN_MODELS = ('calendar.event', 'dms.file', 'website.page',
                    'helpdesk.team', 'helpdesk.ticket')
```

Listan namnger bara modeller vars brygga bor i ett **annat repo**
(`calendar_ai`, `dms_ai`, `website_ai`, `helpdesk_ai`). Modeller kärnan äger
(`ai.*`) eller får från sina egna beroenden (`base`, `mail`, `hr`,
`web_pwa_push`) prövas inte — de är alltid tillgängliga.

Testet faller om en oskyddad referens läggs tillbaka (verifierat med den
oskyddade varianten 2026-10-07).

### Sammanfattningen kan anpassas per ägare

`_okf_build_summary()` och `_okf_summary_source()` tar ett valfritt
`owner_vals`-argument, så en brygga kan sammanfatta olika per ägare — t.ex.
utelämna en delad vy:s uppgifter i ett personligt koncept.

**Bakåtkompatibilitet:** åtta bryggor implementerade
`_okf_summary_source(self)` utan argumentet. `_okf_call_summary_source()`
inspekterar signaturen en gång per klass och skickar ägaren bara om metoden
accepterar den — annars hade samtliga kraschat med `TypeError`.

## Batchad embedding vid OKF-indexering (18.0.1.308)

`_okf_upsert()` producerade **en embedding per koncept** — ett HTTP-anrop i
taget. En post med N ägare betalade N anrop. Mätt 2026-10-08:

```
   6 separata anrop:  2,91 s   (0,485 s per text)
   1 batch-anrop:     0,32 s   (0,053 s per text)
   -> 9,1x, identiska vektorer
```

Kostnaden uppmärksammades när `calendar_ai` började ge **N+1 koncept per
händelse** (ett `company` + ett `personal` per deltagare). 21 000 händelser
med 5 deltagare blev 126 000 anrop, ~14 timmar.

### Var batchningen sitter — och varför inte i `_okf_upsert`

```
   _okf_index_record()
     bygger texten for varje (agare, sprak)      <- har ar alla texter kanda
     _okf_embed_texts(texts)                     <- ETT anrop (delat vid taket)
     for plan in plans:
       _okf_index_record_one(embedding=vec)      <- far den fardiga vektorn
         _okf_upsert(embedding=vec)              <- skickar vidare
```

`_okf_upsert` anropas en gång per (ägare, språk) och embeddar inne i sig.
Det finns ingen plats i den kedjan där flera texter är kända samtidigt —
utom hos anroparen. `_okf_index_record` är den platsen.

`_okf_upsert` hade redan ett `embedding`-argument (det används av
efterfyllnaden), så ingen ny mekanism behövdes — bara en ny anropare.

### Textformen måste vara exakt `_produce_embedding`s

```python
   text = ' '.join(filter(None, [title, summary])).strip()
```

Samma text som den enskilda vägen skulle embeddat, annars blir vektorerna
inte identiska. `title` är `display_name[:120]` — samma som `_okf_upsert`
sätter.

### Fallback — en trasig batch får inte tysta

`_get_embedding_batch()` avvisar **hela** batchen vid fel antal vektorer
(`[None] * n`, "hellre tomt än felkopplat"). Då får varje koncept `None` och
`_okf_upsert` producerar vektorn själv — exakt beteendet före ändringen.

Vid **partiellt** fel (en text utan vektor) får just den texten `None` och
embeddas enskilt. `embedding_state` sätts likadant i båda vägarna
(`ready`/`pending`), verifierat.

### Batchtaket

`OKF_EMBED_BATCH_MAX = 64`. Bifrost tog 256 texter i ett anrop i mätningen
(1,44 s), men gränsen är **inte dokumenterad** — och en post med många ägare
ska inte riskera ett anrop som faller. 64 är väl bevisat och ger 4 anrop för
256 texter. Taket delar texterna i chunkar och håller ordningen.

### Mätt resultat

```
   5 agare (personal memory):   5 anrop 2,97 s  ->  1 anrop 0,38 s  (7,9x)
   kalenderhandelse, 5 deltagare (6 koncept):
                                6 anrop        ->  1 anrop (6 texter)
```

### Vad som INTE gjordes

Batchning över **cron-varvets poster**. Det kräver att
`_okf_index_record` delas i "bygg texter" och "skriv koncept", och vinsten
är liten: de flesta poster har **en** ägare och gör därför redan ett anrop.
Kalendern (N ägare) löses av batchningen inom posten.
