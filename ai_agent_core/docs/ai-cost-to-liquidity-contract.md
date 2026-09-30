# Kontrakt: AI-kostnad → likviditetsprognos

Bryggan mellan `ai_agent_core` och företagets likviditetsprognos
(`odoo-liquidity`). Definierar vad Odoo-ai-sidan levererar — **inte**
prognosmotorn, som ägs av `odoo-liquidity` (PRD v2.1).

## Källa

`ai.cost.period.line` — aggregerad AI-kostnad per period och valuta.
Byggs av `cron_cost_aggregate` (dagligen) ur `ai.coworker.session.line`.
Idempotent: periodens rader skrivs om, aldrig dubbelräknade.

## Fält (kontraktet)

| Fält | Typ | Betydelse |
|---|---|---|
| `period_start` | Date | Periodens första dag |
| `granularity` | Selection | `day` eller `month` |
| `currency_id` | Many2one | Bolagsvalutan |
| `cost_usd` | Float | **Faktisk kostnad** (USD) — Σ `cost_usd` |
| `cost_company` | Float | Faktisk kostnad i bolagsvalutan, omräknad per **radens datum** |
| `billing_base` | Float | **Debiteringsgrund** — Σ `token_sys` × `sys_multiplier` (inkl. marginal) |
| `line_count` | Integer | Antal rader i perioden |
| `unreported_line_count` | Integer | Rader med `usage_reported=False` |
| `has_unreported` | Boolean | True om någon rad är omätt |
| `partner_id` / `project_id` / `task_id` | Many2one | Attribution (valfritt per gruppering) |
| `context_confirmed` | Boolean | False = okontextad andel (döljs inte) |

## Tre regler en konsument MÅSTE följa

1. **`cost_usd` och `billing_base` är två olika tal.** Den första är vad AI:n
   kostade (USD); den andra är vad kunden debiteras (inkl. Vertel-marginal).
   Att slå ihop dem vore att felaktigt fakturera eller felaktigt prognostisera.
2. **`has_unreported=True` → beloppet är ett minimum.** Visa "minst X kr",
   aldrig ett falskt exakt tal. `usage_reported=False` betyder att providern
   inte rapporterade usage — inte att kostnaden var noll.
3. **Omräkning sker per radens datum.** En senare valutakursändring ändrar
   inte historiska perioder.

## Vad bryggan INTE gör

- Bygger inte prognosmotorn (`odoo-liquidity`).
- Inför inget beroende från `ai_agent_core` till `liquidity_*` — kärnan är
  domän-ren. Aggregatet är den exporterade datakällan.
- Ändrar inte `budget-hard-cap` eller `budget-burn-rate` (token-baserade).
