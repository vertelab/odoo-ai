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
ai_agent_core: 1899 tester, 344 s, 120414 queries
Module ai_agent_core: 11 failures, 9 errors of 500 tests
```

De 500 är modul-fasen; de 1 899 är hela sviten inklusive beroenden.

## Kända fel — accepterade

| Klass | Antal | Orsak | Åtgärd |
|---|---|---|---|
| `TestDefaultModel` | 2 | Ingen provider i testdatabasen | Testet bör skapa sin egen provider i `setUp` |
| `TestProviderPrecheck` | 3 | Samma — ingen modell/provider att prechecka | Samma |
| `TestLineage` | 2 | `suggestion_to_action`-kanten skapas inte | Riktig bugg — utreds |
| `TestHandover` | 1 | Sortering av förslag | Utreds |
| `TestTokenTotals` | 6 | Token-aggregeringen ger andra tal än förväntat | Utreds |
| `TestBudgetBlockMessage` | 3 | Meddelandetexten matchar inte | Utreds |
| `TestRetentionWarning` | 2 | Varningen skapas inte/dupliceras | Utreds |
| `TestRetentionBackfill` | 2 | Backfill räknar/loggar fel | Utreds |
| `TestRetentionTypeChange` | 3 | Omräkning vid typbyte | Utreds |
| `TestOkfAccessResolver` | 2 (ERROR) | Resolvern kastar | Utreds |
| `TestSearchSettings` | 2 | Sökprofilen | Utreds |
| `TestPowerboxScoping` | 2 (ERROR) | Scoping-env | Utreds |
| `TestPiMessageDedup` | 1 | Snabbspåret | Utreds |
| `TestDiscussLearning` | 1 | Cron hoppar över fel | Utreds |
| `TestPgVectorPersistsWithoutPackage` | 1 | Läs tillbaka | Utreds |
| `TestAiMemoryPgvector` | 1 | Sökning utan agent-länk | Utreds |
| `TestNoSilentDownload` | 2 (ERROR) | Nedladdningsskydd | Utreds |
| `TestCoworkerCounters` | 1 | Token-summa | Utreds |
| `TestDailyBudget` | 1 (ERROR) | Dagsbudget | Utreds |
| `TestOutcomeVerification` | 1 (ERROR) | Utfallsverifiering | Utreds |
| `MailChannelCharacterization` | 4 | Karaktäriseringstester skrivna före migreringen | Utreds |
| `ChatChannelCharacterization` | 7 | Samma | Utreds |

## Kända fel — utanför `ai_agent_core`

`setUpClass`-fel i `web`, `web_editor`, `web_pwa_*`, `web_unsplash` m.fl.
(125 st) beror på att `http_port()` kräver en körande server. De räknas
**inte** in i den här baslinjen — de tillhör sina moduler.

## Regeln

En ändring får **inte** öka antalet fel. Ett nytt fel ska antingen åtgärdas
eller läggas till i tabellen ovan med ett skrivet skäl. Att lägga till ett fel
utan skäl är att sänka ribban i smyg.
