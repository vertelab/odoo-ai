# Baslinje för `ai_agent_core`s testsvit

> Mätt 2026-11-02 mot en färsk testdatabas på `luke18`.
> Uppdatera den här filen när ett känt fel åtgärdas — aldrig för att göra
> grinden grön.

## Körkommandot

```bash
sudo -n -u postgres dropdb --if-exists tst_suite
sudo -n -u postgres createdb tst_suite
sudo -n -u postgres psql -d tst_suite \
  -c "CREATE EXTENSION IF NOT EXISTS age; CREATE EXTENSION IF NOT EXISTS vector;"
sudo -n -u odoo odoo --config /etc/odoo/odoo.conf --database tst_suite \
  --no-http --stop-after-init --log-level=test \
  --init ai_agent_core --without-demo=all --test-enable \
  --logfile=/tmp/suite.log
grep -E 'Module ai_agent_core: [0-9]+ failures' /tmp/suite.log
```

**Krav:** `CREATE EXTENSION age, vector` FÖRE `--init`. Utan dem kraschar
registry-load på `schema "ag_catalog" does not exist`.

**Kör inte** `--test-enable` mot kod som skapar OKF-koncept utan att mocka
`_embedding_provider`. Utan mock går anropet till Bifrost, blockeras av
testramverket, och retryar i oändlighet (mätt: 292 blockerade anrop, 106
omgångar av "försök 1/3").

## Uppmätt baslinje

**Mätt om 2026-11-05** efter att ALLA 15 saknade testfiler importerats i
`tests/__init__.py` och rättats (se nedan).

```
ai_agent_core: 2293 tester, 119 s, 140296 queries
Module ai_agent_core: 1 failures, 4 errors of 649 tests
```

De 649 är modul-fasen; de 2 293 är hela sviten inklusive beroenden.

⚠ **Den tidigare baslinjen (1 920 tester, 0 fel) var mätt mot en ofullständig
svit.** 15 testfiler saknades i `tests/__init__.py` och kördes aldrig. Att
siffran nu är 2 293 är inte en regression — det är de tester som alltid
funnits men varit osynliga. De 13 sista filerna gav 44 fel när de
importerades; samtliga var antingen döda tester eller riktiga kodfel (se
"Fynd när de 13 filerna importerades").

## Kända fel

Fyra kända fel, alla dokumenterade med orsak. **Ingen ändring får öka antalet.**

| Test | Fel | Orsak |
|---|---|---|
| `TestClassifyCacheWeak.test_entry_disappears_when_cursor_is_gc` | `3 != 1` | `_CLASSIFY_CACHE` är modulglobal och delas mellan tester i samma process — en tidigare testpost ligger kvar. Testet antar en tom cache. |
| `TestLineage` (3 st) | `ValueError: Wrong value for suggestion_type: 'project.task'` | Testet skickar ett modellnamn där fältet väntar en `Selection`-nyckel. Testfixturen är fel, inte koden. |
| `MailChannelCharacterization.test_reply_is_sent_via_send_mail_reply` | `0 != 1` | Karaktäriseringstest mot en patchad `run()`; anropet når inte `send_mail_reply` i den här miljön. |
| `MailChannelCharacterization.test_reply_is_truncated_to_4000_chars` | följdfel av ovanstående | Samma rot. |
| `TestCompactionWithoutSession` (setUpClass) | `'PreforkServer' object has no attribute 'httpd'` | `HttpCase` kräver en körande HTTP-server. Räknas normalt utanför baslinjen (se nedan) men ligger i `ai_agent_core` så det syns här. |

De 20 fel som fanns när baslinjen först skrevs (2026-11-02) är åtgärdade:

| Kategori | Åtgärd |
|---|---|
| Miljöberoende (`TestDefaultModel`, `TestProviderPrecheck`) | Testerna skapar sin egen provider/modell — `_ensure_test_provider_and_model()` |
| Fixture-kollisioner (`TestTokenTotals`, `TestCoworkerCounters`) | Sekvensen allokeras som produktionen gör (`_next_session_sequence`) |
| Skrivning till icke-lagrade computes (`TestBudgetBlockMessage`) | Testerna skapar de sessionsrader som computes summerar |
| `assertLogs` fångar inte addon-loggers | `_capture_okf_logs()` öppnar propagation och nivå |
| Karaktäriseringstester (mail/chat) | Fake `run()` patchades på klassen → fick `self` som första argument |
| `TestLineage` | Kedjan satte `current_*` inne i kantloopen och hoppade fel |
| `TestHandover` | `write_date` är `automatic` — tidsstämpeln sätts med rå SQL |
| `TestPgVector*` | `convert_to_cache` prövas direkt; ingen `cr.rollback()` i test |
| `TestPowerboxScoping` | Hoppar över när `sale.order` inte är installerad |

## Fynd när de 13 filerna importerades (2026-11-05)

De 44 felen var inte brus. De föll i tre klasser:

**Riktiga kodfel (4) — åtgärdade i källkoden:**

| Fel | Konsekvens |
|---|---|
| `ai_personal_goal.search_for_user()` sorterade på `deadline` | Fältet heter `time_bound` → `ValueError` varje anrop med ett mål i DB |
| `_seed_memory_settings()` anropades bara i mall-kopieringsvägen | En medarbetare med vanlig identitet fick **inga memory_scopes** → injektionen läste ingenting |
| `memory_profile`-seedningen såg `not self.memory_profile` | Fältet har default `'balanced'` och är aldrig tomt → identitetens profil nådde aldrig fram |
| `run()` prövade budgeten EFTER provider-upplösning | En medarbetare som nått taket fick provider-fel i stället för "Budget slut" (och krävde en provider för att neka) |

**Döda tester (mekaniska API-byten) — rättade:**

- `invalidate_cache()` → `invalidate_model()` (borttaget i Odoo 17)
- `ai.coworker.agent.quest_id` → `coworker_id` (bytt i `ai.quest → ai.coworker`)
- `add_memory()` returnerar recordset, inte id
- `source_ref` är `Reference` med begränsad selection — `res.partner` ej tillåten
- `supersedes_id` är avvecklad med flit (konceptet uppdateras på plats)
- Session-rader kräver explicit `sequence` (`UNIQUE(session_id, sequence)`)
- `build_request()` saknade `headers=`-parametern i en fake
- `get_cheapest_model()` kräver `request.env` → testet prövar frågan direkt
- Beroende moduler (`sale`, `crm`, `product`) kan saknas → guardas
- Data-XML äger redan de URL:er/nycklar två tester antog var lediga

**Tester som beskröev ett önskemål, inte koden — rättade till vad som
faktiskt gäller:** `_check_quest_error` auto-skapa en agent, avdelning
injiceras inte i prompten, partnernamnet bär badge-prefix.

## Regeln

En ändring får **inte** öka antalet fel. Ett nytt fel ska antingen åtgärdas
eller läggas till i en tabell här med ett skrivet skäl. Att lägga till ett fel
utan skäl är att sänka ribban i smyg.

## Kända fel — utanför `ai_agent_core`

`setUpClass`-fel i `web`, `web_editor`, `web_pwa_*`, `web_unsplash` m.fl.
(125 st) beror på att `http_port()` kräver en körande server. De räknas
**inte** in i den här baslinjen — de tillhör sina moduler.

## Regeln

En ändring får **inte** öka antalet fel. Ett nytt fel ska antingen åtgärdas
eller läggas till i tabellen ovan med ett skrivet skäl. Att lägga till ett fel
utan skäl är att sänka ribban i smyg.
