# Felkontraktet — en regel per feltyp

> **Varför denna fil finns.** Pi Durable (Earendil, 2026-10) har ett avsnitt
> i sin spec som heter *Failures* med en enda princip: **en regel per typ av
> fel, så att en host vet vad som händer utan att läsa kod.** Det är värt att
> kopiera. `ai_agent_core` har felkontraktet för *verktyg* (MODULE.md,
> `ToolError.to_json()`), men inget samlat kontrakt för *systemet* — vad som
> händer när providern fallerar, när persistensen fallerar, när en hook
> kastar, eller när turen avbryts. Denna fil är det kontraktet.
>
> Referens: `packages/durable/docs/spec.md` §10.1 (Failures) och §12
> (API footguns), github.com/earendil-works/pi.

---

## 1. Grundprincipen

**Ett fel ska stanna vid sin enhet.** Frågan är alltid: *vem äger felet, och
hur långt får det färdas?*

| Nivå | Fel | Konsekvens |
|---|---|---|
| **Provider** | LLM-anropet fallerar | Turen får `finish_reason=error`, svaret persisteras ändå |
| **Verktyg** | Ett verktyg kastar | Verktygsresultatet blir ett fel — turen fortsätter |
| **Hook** | En hook kastar | Rapporteras och hoppas över — utom `beforeTool`, som blockerar |
| **Persistens** | Sessionsraden kan inte skrivas | **Turen rullas tillbaka** — inget halvt svar |
| **Session** | Sessionen kan inte skapas | Anropet avvisas innan LLM ens kontaktas |

Regeln är densamma som Pi Durable:s: **användarkod faller bara sin egen
enhet; infrastrukturfel får större konsekvenser.** Skillnaden är att Pi
Durable gör storage-fel *fatala för hela Sessionen* (stäng och återöppna),
medan vi gör persistensfel fatala för *turen* — Odoos transaktion är
enheten, inte processen.

---

## 2. Providerfel

Providern är den enda externa parten. Den kan svara, neka, eller tystna.

### 2.1 Avslag (`GatewayRejection`) — retrya ALDRIG

Gatewayen avvisade anropet strukturerat. `retryable=False`, alltid.

| `reason` | Vad det betyder | Vad användaren ska göra |
|---|---|---|
| `combo_forbidden` | Modellen är inte entitled för nyckeln | Kontrollera modellnamnet i gatewayens katalog |
| `model_not_found` / `not_found` | Modellen finns inte hos leverantören | Rätta modellnamnet |
| annat | Gatewayens egen orsakstyp | Läs `detail` (max 300 tecken) |

**Varför detta är en egen klass:** utan den visades *"Anslutningen till
AI-servern bröts"* för ett 403 `combo_forbidden` — användaren fick ingen
ledtråd och coworkern gissade fel orsak. Ett avslag är inte ett
anslutningsfel.

### 2.2 Retrybara fel

`_is_retryable()` — enda sanningen om vad som får försökas igen:

```python
ProviderError.retryable          # explicit flagga på felet
httpx.HTTPStatusError: 429, >=500
httpx.ConnectError, httpx.TimeoutException
```

`DEFAULT_RETRY`: 3 försök, exponentiell väntan (1–30 s), `reraise=True`.

### 2.3 Den enda 400:an som retryas

`_is_stream_options_rejection()` skiljer *"providern stödjer inte
`stream_options`/`include_usage`"* från alla andra 400:or (saldofel, okänd
provider, modellfel).

**Historien (T/50601):** `_iter_stream_retry` retryade **blint** på varje
400. Villkoret krävde bara att bodyn hade `stream_options` — och eftersom
`_stream_openai_compat` alltid sätter `include_usage=True` var det alltid
sant. Följden: dubbel last, längre svarstid, och 400:an nådde ändå
användaren.

**Fail-safe:** kan texten inte läsas retryas **inte**. Vi retryar hellre för
lite än för mycket.

---

## 3. Verktygsfel

Fullt kontrakt i MODULE.md (`ToolError.to_json()`). Sammanfattat:

- `retryable=True` — anroparen kan rätta sig: saknat argument, fel format,
  okänt fält.
- `retryable=False` — nytt försök hjälper inte: resursen finns inte, okänd
  modell, internt fel.
- **Tomt argument är ett SAKNAT argument.** `describe_model()` gav
  `"Unknown model: "` — vilket inte namnger vad som saknas.
- **Fel verktyg pekar på rätt verktyg.** `odoo_write` avvisar HTML-fält och
  pekar på `odoo_create` i stället för att bara neka.

Ett verktygsfel **avslutar aldrig turen.** Modellen får felet som
verktygsresultat och kan välja ett annat spår.

---

## 4. Persistensfel — turen rullas tillbaka

**Regel:** ett providerfel propagerar **efter** persistensen, aldrig före.

**Historien (T/50601, T/50603):** `raise _stream_err[exc]` stod **före**
`_persist_stream_tool_lines` och `_persist_stream_answer`. Ett providerfel
hamnade därför direkt i `except`-grenen, och turen lämnades med **enbart en
user-rad** — tråden såg "stannad" ut och användaren fick inget svar (session
23584).

**Nu:** turen persisteras alltid. `_persist_stream_answer` är idempotent och
skriver en assistant-rad med felet i sessionsmetadata (`error_detail`,
`finish_reason=error`). **Först därefter** kastas felet vidare, så klienten
fortfarande får sitt error-event.

**Varför:** en halvskriven tur är värre än en felmarkerad tur. Den som
öppnar sessionen i efterhand måste kunna se *att* något gick fel, inte tro
att inget hände.

---

## 5. Transaktionen vid runda-gränser

**Regel:** turen håller **inte** sin databastransaktion öppen medan den
väntar på LLM.

**Historien (T/50690, T/50723):** verktygen kör på turens cursor, så varje
tabell de **läst** behåller ACCESS SHARE till turen är slut — och en
streaming-tur kan pågå i minuter (upp till 300 s per LLM-chunk; en historisk
session körde 21 min). Mätt på ledningssystem: en AI-turs transaktion höll
`AccessShareLock` på `project_project` + `project_task` + `ai_coworker` i
35–39 s, idle in transaction.

**Konsekvens:** en `ALTER TABLE` behöver ACCESS EXCLUSIVE, och Odoo avbryter
låsväntan efter 15 s (`modules/loading.py`: `SET SESSION lock_timeout = 15s`).
DDL-uppgraderingar krävde därmed nedtid.

**Lösning:** `AgentConfig.checkpoint_callback` anropas vid de två punkter
loopen **inte** behöver sin transaktion — före varje provider-anrop
(`round-start`) och när rundans verktyg kört klart (`tools-done`). Loopen
förblir domän-ren (ingen Odoo-import); **ägaren committar**.

`_build_loop` är choke point: **alla vägar går genom den** (cron, mejl,
webhook, knapp-åtgärder, automation, linear-inline, single, supervisor). En
rad täcker dem alla.

**Semantik:** verktygens skrivningar och sessionsraderna blir **durable per
runda** i stället för allt-eller-inget per körning. Det är samma
per-handling-semantik en användare får i UI:t — och det är vad som gör att
transaktionen kan släppas.

**Känd lucka:** `LinearLoop` tar emot `outcome_callback` i `_build_loop`-anropet
men dess `__init__` har ingen sådan parameter — den linjära vägen kastar
`TypeError` redan före fixen. `SupervisorLoop`s router bygger sin egen
`AgentConfig` utan callbacks (enkel routing-call).

---

## 6. Hook-fel

| Hook | Kastar | Konsekvens |
|---|---|---|
| `beforeTool` | Ja | **Blockerar anropet** |
| övriga hooks | Ja | Rapporteras och hoppas över |
| `outcome_callback` | Ja | `_logger.exception` — turen fortsätter |
| `checkpoint_callback` | Ja | `_logger.exception` — turen fortsätter |

**Regel:** en hook som inte *måste* fälla sin anropare ska **containas**.
Undantaget är `beforeTool`, som finns just för att kunna neka.

---

## 7. Avbrott och finish_reason

`finish_reason` är sessionens **dödsattest**. Rangordningen
(`FINISH_REASON_RANK`) avgör vem som vinner när två källor säger olika —
**högre vinner, svagare skriver aldrig över starkare:**

```
0  stop, idle, closed, new_session     — normalt slut
1  cancelled, interrupted              — avbrutet
2  max_rounds                          — loopen tog slut
3  content_filter                      — providern stoppade
4  length                              — trunkerat
5  error                               — fel
```

**Varför rangordning:** gatewayens `stop_reason` kan veta något Odoo inte
visste — t.ex. att modellen trunkerades (`length`) medan sessionen stängdes
som `max_rounds`. Utan rangordningen hade det svagare värdet skrivit över.

`_raise_finish_reason(candidate)` höjer bara; tom/okänd kandidat ignoreras.

---

## 8. Sessionens livscykel

| Steg | Fel | Konsekvens |
|---|---|---|
| `_lookup_or_create_pi_session` | Okänt `pi_session_id` | **Ny** session skapas — aldrig fallback |
| `_find_or_create_coworker_session` | Inget `pi_session_id` | Fallback: närmast aktiva (samma coworker+user, 20 min) |
| `mark_done(reason)` | — | Sätter `finish_reason`, `end_date`, push-notis |
| `mark_interrupted()` | — | `finish_reason=interrupted` |
| `_cron_close_idle_sessions` | — | Stänger idle sessioner |

### 8.1 Fallback-fällan (T/50830)

**Regel:** ett skickat men okänt `pi_session_id` får **aldrig** falla tillbaka
på "närmast aktiva". Det skulle knyta en ny Pi-session till en annan
Pi-sessions Odoo-session och tappa 1:1-kopplingen.

Fallbacken är **reserverad för klienter som inte har något Pi-id alls**
(web_ui, mail, powerbox).

**Bevisat problem:** en klient som *inte* skickar `pi_session_id` alls får
fallbacken — och två orelaterade anrop inom 20 minuter hamnar i **samma**
session. Uppmätt: `line_count` 6 → 12 på en session som inte var klientens.

**Lösning:** skicka `pi_session_id` i bodyn, eller markören
`{pi session: <uuid>}` i systemprompten (`_extract_pi_session_marker` —
finns för proxad trafik som strippar okända fält).

---

## 9. Namngivning — tre axlar, ett ord

**Fällan:** ordet "openai" betyder tre olika saker på tre olika ställen.

| Fält | Betyder | Exempel |
|---|---|---|
| `init_type=openai_api` | **Kanal** — extern API-klient | Etikett: *Externt API (/ai/v1)* |
| `ai.provider.api_style=openai` | **Protokoll** — /chat/completions | vs `anthropic` (/v1/messages) |
| `ai.provider.provider_type=openai` | **Leverantör** — OpenAI själva | Tom i vår drift |

**Konsekvensen:** etiketten `(openai_api, OpenAI API)` fick varje
användare att tro att GPT körde, när den bara betydde "nåbar via ett
OpenAI-*kompatibelt* HTTP-gränssnitt". Rättat 2026-10-13 till
**Externt API (/ai/v1)**.

**Regel:** en kanal är inte en leverantör. Namnge dem olika.

---

## 10. Vad vi INTE gör (och varför)

I Pi Durable:s anda — explicita icke-mål är ett kontrakt:

- **Ingen automatisk retry av persistensfel.** Ett misslyckat sessionsskriv
  är ett fel som ska synas, inte döljas av ett nytt försök.
- **Ingen tyst fallback till en annan modell.** Ett provideravslag
  propagerar. Bifrost gör fallback på *sin* nivå — vi gissar inte.
- **Ingen tvingad terminering av icke-kooperativ hook-kod.** En hook som
  ignorerar sin signal håller turen; vi dödar den inte inifrån.
- **Ingen halvskriven tur.** Antingen persisteras svaret, eller så
  felmarkeras det. Aldrig tyst bortfall.

---

## Referenser

| Källa | Vad |
|---|---|
| `MODULE.md` §Verktygsfel | `ToolError.to_json()` — verktygskontraktet |
| `core/provider.py` | `ProviderError`, `GatewayRejection`, `_is_retryable`, `_is_stream_options_rejection` |
| `models/ai_session.py` | `FINISH_REASON_RANK`, `_raise_finish_reason`, `_lookup_or_create_pi_session` |
| `controllers/stream.py` | Persistens-ordning, `_run_coworker_chat` (ren generation) |
| T/50601, T/50690, T/50723 | Providerfel, transaktionsläckage |
| T/50830 | Pi Durable-undersökningen, fallback-fällan |
| `packages/durable/docs/spec.md` §10.1 | Pi Durable — Failures (förebilden) |
