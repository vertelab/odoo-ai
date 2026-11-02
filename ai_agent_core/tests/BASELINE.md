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

```
ai_agent_core: 1920 tester, 347 s, 124103 queries
Module ai_agent_core: 0 failures, 0 errors
```

De 500 är modul-fasen; de 1 920 är hela sviten inklusive beroenden.

## Kända fel

**Inga.** Baslinjen är noll sedan 2026-11-02.

De 20 fel som fanns när baslinjen skrevs är åtgärdade:

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
