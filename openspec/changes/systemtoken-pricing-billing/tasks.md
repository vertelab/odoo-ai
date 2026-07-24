# Tasks: Systemtoken Pricing & Billing

## Horisont 1: Systemtoken-grund (NU — 1-2 veckor)

Mål: Byt ut hårdkodad `token_sys = token × 12` mot modell-specifik multiplikator. Allt som behövs för att kunden ska se systemtoken-förbrukning per session och per quest.

### T1.0 — Bifrost model sync → populera `ai.model`
- [ ] Anropa `GET /v1/models` på Bifrost (`http://192.168.11.150:8080/v1/models`)
- [ ] Skapa/uppdatera `ai.model`-poster från Bifrost-svaret
- [ ] Sätt `sys_multiplier` till default-värden vid första sync:
  - deepseek → 1.0, gpt-4o-mini → 1.5, gpt-4o → 5.0, claude → 6.0, embedding → 0.1
  - Övriga modeller → 1.0 (admin justerar manuellt)
- [ ] Möjlighet att köra om sync ("Hämta modeller från Bifrost"-knapp)
  - Uppdaterar `ai.model`-listan (nya modeller läggs till, saknade inaktiveras)
  - **Rör inte** `sys_multiplier` på redan existerande modeller (bevara admin-justeringar)
- [ ] Sätt `provider_cost_1M` där Bifrost rapporterar priser
- **Beroenden:** Inga (använder befintlig `BifrostProvider.fetch_models()`)
- **Tid:** ~1.5 timmar

### T1.1 — `ai.model` — sys_multiplier + admin-vy
- [ ] Lägg till `sys_multiplier` (Float, default 1.0) på `ai.model`
- [ ] Lägg till `provider_cost_1M` (Float) på `ai.model` (admin-insyn)
- [ ] Skapa admin-vy: lista med modell, multiplikator, provider-kostnad
- [ ] Kund-vy: visa endast `sys_multiplier` som text ("6× systemtokens")
- **Beroenden:** Inga (men T1.0 populärar data)
- **Tid:** ~30 min

### T1.2 — `ai.quest.session.line` — sys_multiplier + compute token_sys
- [ ] Lägg till `sys_multiplier` (Float, store=True) på `ai.quest.session.line`
- [ ] Ändra `token_sys` från Integer (write) till Integer (compute, store=True)
- [ ] Implementera `_compute_token_sys`: `token × sys_multiplier`
- [ ] Uppdatera `new_line()`: slå upp `ai.model` via `model_real`, sätt `sys_multiplier`
- [ ] Fallback: om `ai.model` inte hittas → `sys_multiplier = 1.0`
- [ ] **Notera:** Befintliga session lines med `token_sys = token × 12` lämnas orörda.
  Migrering hanteras separat när allt är klart.
- **Beroenden:** T1.1 (sys_multiplier måste finnas på ai.model)
- **Tid:** ~2 timmar

### T1.3 — `ai.quest` — started_mtokens compute
- [ ] Lägg till `started_mtokens` (Integer, compute) på `ai.quest`
- [ ] Implementera `_compute_started_mtokens`: `ceil(session_line_count / 1_000_000)`
- [ ] `session_line_count` finns redan — den räknar automatiskt rätt när `token_sys` är compute
- **Beroenden:** T1.2 (token_sys måste vara compute för att session_line_count ska funka)
- **Tid:** ~30 min

### T1.4 — Systemtoken i web-chat UI (per session)
- [ ] Visa systemtoken-förbrukning i chat-foten: "Session: 14 200 systemtokens"
- [ ] Visa modellens multiplikator i model-selectorn: "Claude (6×)"
- [ ] Uppdatera live under sessionens gång (poll eller SSE-event med token count)
- **Beroenden:** T1.2, web-chat-threads-memory
- **Tid:** ~2 timmar

### T1.5 — Systemtoken i quest backend-vy
- [ ] Smartknapp på ai.quest: "Månadsförbrukning" → öppnar session_line-lista filtrerad på månad
- [ ] Visa `session_line_count` och `started_mtokens` i quest-formuläret
- [ ] Färgindikator: grön < 80%, gul 80-100%, röd > 100% av cap (om cap finns)
- **Beroenden:** T1.3
- **Tid:** ~1 timme

### T1.6 — Tester
- [ ] Test: `sys_multiplier` sätts från `ai.model` vid `new_line()`
- [ ] Test: fallback till 1.0 när model inte hittas
- [ ] Test: `_compute_token_sys` räknar rätt (token × multiplier → integer)
- [ ] Test: `_compute_started_mtokens` rundar upp korrekt
- [ ] Test: `session_line_count` ackumulerar token_sys över månaden
- **Beroenden:** T1.2, T1.3
- **Tid:** ~1 timme

---

## Horisont 2: Kommersiell kontroll (NÄSTA — 3-4 veckor)

Mål: Kunden kan sätta tak, få varningar, Vertel får larm. Faktureringsunderlag exponeras.

### T2.1 — `ai.quest` — monthly_cap + varningsfält
- [ ] Lägg till `monthly_cap_mtokens` (Integer, default=0, 0=obegränsat)
- [ ] Lägg till `cap_warning_sent` (Boolean, default=False)
- [ ] Lägg till `cap_exhausted` (Boolean, default=False)
- [ ] Validering: `monthly_cap_mtokens >= 0`, heltal
- [ ] Kund-vy: visa tak + aktuell förbrukning i quest-formulär
- **Beroenden:** T1.3 (started_mtokens)
- **Tid:** ~1 timme

### T2.2 — Cap enforcement logic
- [ ] Implementera `_check_cap()` på `ai.quest` — anropas efter varje session_line-skapande
- [ ] Vid 80% av cap: sätt `cap_warning_sent = True`, skicka varning
- [ ] Vid 100% av cap: sätt `cap_exhausted = True`, **hard stop** — agenten vägrar nya anrop
- [ ] Kunden kan höja taket → `cap_exhausted = False`, `cap_warning_sent = False`
- [ ] Edge case: kan inte sänka taket under redan förbrukat (`started_mtokens`)
- **Beroenden:** T2.1
- **Tid:** ~2 timmar

### T2.3 — Kundnotifiering
- [ ] Vid 80%: posta meddelande i questens discuss.channel
- [ ] Vid 100%: posta meddelande + markera quest som "tak överskridet"
- [ ] Meddelande-innehåll: "Din AI-medarbetare X har använt Y% av månadstaket (Z M-tokens)."
- [ ] Länk till quest-inställningar där kunden kan höja taket
- [ ] Endast discuss.channel initialt — email kan läggas till senare vid behov
- **Beroenden:** T2.2
- **Tid:** ~1 timme

### T2.4 — Cap management UI (kund)
- [ ] Vy i quest-formulär: aktuellt tak, förbrukning, prognos ("tak nås om X dagar")
- [ ] "Ändra tak"-knapp → popup med slider/input
- [ ] Historik: logga tak-ändringar (vem, när, gammalt → nytt)
- **Beroenden:** T2.1, T2.2
- **Tid:** ~2 timmar

### T2.5 — `ai_agent_zabbix` — modul
- [ ] Skapa modul: `ai_agent_zabbix` med `__manifest__.py` (depends: `ai_agent_core`)
- [ ] Ingen `zabbix_base` behövs — Zabbix 7.0 använder JSON-RPC 2.0 över HTTPS
- [ ] Återanvänd mönster från `paperclip/employees/files/zabbix_client.py`:
  - URL: `zabbix.vertel.se/api_jsonrpc.php`
  - Auth: API token
  - Metod: `_zabbix_call(method, params) → result`
- [ ] Modell `ai.zabbix.config` — Zabbix URL, API-token (från pillar)
- [ ] Metod `send_event(name, value, tags)` — skapar Zabbix event via `event.create`
- [ ] Konfigurations-vy för admin (URL + token)
- **Beroenden:** Salt pillar för Zabbix API-token
- **Tid:** ~3 timmar

### T2.6 — Zabbix-event för cap exceeded
- [ ] Trigger: när `cap_exhausted` sätts till True → skapa Zabbix event
- [ ] Event-data: `name="AI cap exceeded: {quest.name}"`, `value={started_mtokens}`, `tags={quest_id, customer, cap}`
- [ ] Verifiera att event syns i Zabbix UI under "Problems"
- **Beroenden:** T2.5, T2.2
- **Tid:** ~1 timme

### T2.7 — Billing data exposure
- [ ] Skapa `ai.quest.billing_data` compute-funktion som returnerar JSON:
  - `active_quests`: antal aktiva quests (ai-medarbetare)
  - `total_users`: antal aktiva användare
  - `month`: innevarande månad
  - `per_quest`: [{quest_id, name, started_mtokens, cap, exhausted}]
- [ ] Exponera via standard Odoo XMLRPC — `models.execute_kw('read')` räcker
- [ ] Dokumentera fält för fakturerings-odoons utvecklare
- **Beroenden:** T1.3, T2.1
- **Tid:** ~1 timme

### T2.8 — Månadsöversikt (quest smartknapp)
- [ ] Smartknapp på ai.quest: "Senaste månaden" → filtrerad vy
- [ ] Visa: totalt systemtokens, per-modell-fördelning, antal sessioner
- [ ] Jämför med föregående månad (trendpil)
- **Beroenden:** T1.5
- **Tid:** ~2 timmar

---

## Horisont 3: Full plattform (SENARE — 1-3 månader)

Mål: Komplett systemtoken-ekonomi med dashboard, historik, modell-jämförelser.

### T3.1 — Modell-priser i web-chat model selector
- [ ] Visa `sys_multiplier` som prisinformation i model-selectorn
- [ ] Tooltip: "1 verklig token = X systemtokens med denna modell"
- [ ] Sortera modeller efter pris (billigast först)
- [ ] Visa estimerad sessionskostnad baserat på vald modell
- **Beroenden:** T1.4, web-chat-threads-memory
- **Tid:** ~2 timmar

### T3.2 — Agent budget dashboard (Odoo backend)
- [ ] Dashboard-vy: per-agent förbrukning, per-modell, per-månad
- [ ] Stapeldiagram: månadsförbrukning över tid
- [ ] Cirkeldiagram: fördelning per modell
- [ ] Export-knapp (CSV/Excel)
- **Beroenden:** T2.8
- **Tid:** ~4 timmar

### T3.3 — Bifrost usage sync
- [ ] Pull-modell: Odoo cron pollar Bifrost `GET /v1/models` + usage-endpoint (om tillgängligt)
- [ ] Vid sync: jämför Bifrost-modellista med `ai.model`
  - Nya modeller → skapa med `sys_multiplier = 1.0` (admin justerar)
  - Saknade modeller → markera som inaktiva
  - **Rör inte** `sys_multiplier` på existerande modeller
- [ ] Knapp "Synca modeller från Bifrost" i admin-vyn (samma som T1.0 men för manuell omkörning)
- [ ] Reconcile: jämför Bifrost total-tokens med session_line-summa
- [ ] Larm vid differens > 5%
- **Beroenden:** T1.0, Bifrost usage-API (om tillgängligt)
- **Tid:** ~4 timmar

### T3.4 — Direct provider key management (admin)
- [ ] Admin-vy: lista kunders direkta API-nycklar (maskade)
- [ ] Lägg till/förnya/återkalla nycklar
- [ ] Status: aktiv / rate-limited / spärrad
- [ ] Logg: vilken quest använder vilken nyckel
- **Beroenden:** DirectProvider i ai-agent-core-loop
- **Tid:** ~3 timmar

### T3.5 — Månadsreset och arkivering
- [ ] Cron: vid månadsskifte, skapa "månadsbokslut" för varje quest
- [ ] Arkiv-modell: `ai.quest.monthly_summary` — quest_id, month, started_mtokens, cost
- [ ] `session_line_count` filtrerar redan på innevarande månad — inget behöver nollställas
- [ ] `create_date`-filter är tillräckligt för periodisering — ingen fysisk arkivering behövs initialt
- **Beroenden:** T1.3
- **Tid:** ~2 timmar

### T3.6 — Systemtoken historisk rapportering
- [ ] Rapport-vy: valbar period (månad/kvartal/år), per quest, per kund
- [ ] Trender: jämför perioder, visa tillväxt/minskning
- [ ] Export för fakturering (CSV med exakt det format ekonomisystemet vill ha)
- **Beroenden:** T3.5, T2.7
- **Tid:** ~3 timmar

---

## Sammanfattning

| Horisont | Tasks | Estimerad tid |
|----------|-------|---------------|
| H1: Grund | T1.0–T1.6 (7 tasks) | ~8.5 timmar |
| H2: Kontroll | T2.1–T2.8 (8 tasks) | ~13 timmar |
| H3: Plattform | T3.1–T3.6 (6 tasks) | ~18 timmar |
| **Totalt** | **21 tasks** | **~39.5 timmar** |

---

## Besvarade frågor

| Fråga | Svar |
|--------|------|
| Migrera befintliga session lines? | Nej, hanteras separat när allt är klart |
| Zabbix-version + integration? | Zabbix 7.0, JSON-RPC 2.0, återanvänd `zabbix_client.py`-mönster från paperclip |
| Fakturerings-API? | Standard Odoo XMLRPC `read()` räcker |
| Bifrost model sync? | Pull via `/v1/models`, initial config av `sys_multiplier`, admin kan köra om |
| Notifiering: discuss + email? | Endast discuss.channel initialt |
| Arkivering av gamla session_lines? | `create_date`-filter räcker, ingen fysisk arkivering behövs nu |
