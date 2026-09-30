# AI: Powerbox & Transcript (`ai_agent_transcript`)

Powerbox-mönstret portat till **ai_agent_core**-runtimen. Modulen kopplar
Odoos interface-punkter (HTML-fält, mail-composer, chatter-knapp, systray,
rösttranskription) till `ai.coworker` med automatisk
transcript-/kontextinjektion.

> **Beroende:** `ai_agent_core` — INTE legacy `ai_agent`/`ai_agent_context`.
> Modulen är en brygga: kärnan (`ai_agent_core`) är domän-ren och nämner
> aldrig denna modul.

---

## 1. `ai.composer` — interface → coworker

Varje composer mappar en **interface-punkt** till en **coworker** med
prompt och quick-actions.

| Fält | Beskrivning |
|---|---|
| `name` | Regelnamn (visas i konfigurations-UI) |
| `interface_key` | Var i UI:t den triggas (se nedan) |
| `focused_models` | Begränsa till modeller; **tomt = alla modeller** |
| `coworker_id` | AI-medarbetaren som hanterar anropet |
| `default_prompt` | Systemprompt vid anrop härifrån |
| `available_prompts` | Quick-actions, **en per rad** |
| `is_system_default` | System-default — kan inte raderas/inaktiveras |

### Interface-keys

| Key | Yta |
|---|---|
| `html_field_record` | Skriv i ett HTML-fält |
| `mail_composer` | Skriv ett e-postmeddelande |
| `html_field_text_select` | Skriv om markerad text |
| `chatter_ai_button` | Hjälp om en post (chatter) |
| `systray_ai_button` | Fråga AI (systray) |
| `voice_transcription_component` | Sammanfatta rösttranskription |
| `powerbox_chat` | Powerbox Chat Quest |
| `powerbox_channel` | Powerbox Channel Quest |

### Uppslagning

`find_composer(interface_key, model_name)` väljer bästa matchning:

1. Composer vars `focused_models` innehåller modellen (om angiven)
2. Composer med **tomt** `focused_models` (gäller alla)
3. Annars första kandidaten

### Skydd av system-defaults

Composers med `is_system_default=True` kan inte inaktiveras
(`_check_system_defaults`) eller raderas (`_unlink_except_default_rules`) —
skyddet gäller **även superuser**. `_force_unlink()` finns för medveten
borttagning (admin/migrationer); `copy_data` nollar flaggan vid kopiering.

---

## 2. Transcript-injektion

`ai.coworker.session` får tre fält (i `models/ai_coworker_session.py`):

- `interface_key` — vilken yta sessionen startades från
- `text_selection` — vald text (för omskrivning)
- `frontend_info` — frontend-kontext (JSON)
- `transcript_context` (computed) — byggs av:
  - **rekordfält-JSON** (`_ai_serialize_fields_data`)
  - **chatter** (`_ai_serialize_messages_data`)
  - **frontend-kontext**
  - **vald text** (`text_selection`)

Kontext-rekordet hämtas via `_get_transcript_context_record()` — från
`env.context`-nycklarna `_ai_context_model`/`_ai_context_id` (samma källa
som core `ai.coworker._get_ai_context_record`), med fallback till sessionens
kopplade objekt. OBS: `ai.coworker.session` har medvetet INGA `context_*`-fält
i core.

### Powerbox-hook (D2)

Core:s `powerbox()` känner inte till powerbox-ytan och skapar sessionen utan
transcript-fälten. Bryggan (`models/ai_coworker.py`) override:ar `powerbox()`
och läser `interface_key`/`text_selection`/`frontend_info` från explicita
kwargs eller `env.context` (`_ai_interface_key`/`_ai_text_selection`/
`_ai_frontend_info`). Sessionens `create()`-hook skriver dem på sessionen.
No-op när nycklarna saknas — core-kontraktet är oförändrat.

---

## 3. Mötestext (rösttranskription)

Default-composern **"Voice Transcription — Summarize"**
(`ai_composer_voice_transcript`) sammanfattar rösttranskriptioner till
mötesanteckningar och action items.

Quick-actions: *Summarize this call · Extract action items · Write meeting
minutes · Summarize this prospect call · Write an email recap*.

Flöde: transkription → prompt → coworker → mötesanteckningar.

---

## 4. UI-koppling

`static/src/js/quest_powerbox.js` portar powerbox-JS:en mot
core-endpointen **`/ai/powerbox/run`** (skickar `interface_key`,
`record_model`/`record_id`, `text_selection`, `frontendInfo`). Tjänsten
registreras som `ai_powerbox`.

---

## 5. Deploy

```bash
# Uppgradera (Odoo 18: --update, inte --init)
sudo checkmodule -d <db> -m ai_agent_transcript

# Med tester
sudo checkmodule -d <db> -m ai_agent_transcript -t
```

### openai_api-init

Modulen kräver att `ai_agent_core`s OpenAI-API-controller är aktiv:

- Endpoint: `POST /ai/openai/<coworker_id>/v1/chat/completions`
- Auth: Bearer API-nyckel (`res.users.apikeys`)
- Returnerar `tool_calls` och stödjer HITL-via-tool_calls

Sätts upp i `ai_agent_core` (Settings → AI). Se `ai_agent_core/README.md`
för `openai_api`-raden i init-typs-tabellen.

---

## 6. Tester

```bash
python3 -m unittest ai_agent_transcript.tests.test_transcript
```

Täcker: composer-uppslagning (`find_composer`, specifik > generisk),
transcript_context-bygge (rekord + chatter + text_selection),
powerbox-körning med `interface_key` (mockad provider → session-fälten sätts)
samt regression för core `powerbox()`-signaturen.

---

## Regler (Odoo 18)

- `view_mode: list` (aldrig `tree`)
- Inga `<delete>` på icke-existerande xmlids
- `--update` (inte `--init`) för att tvinga moduluppgradering
- Källkod + commits på engelska; UI-strängar engelska i koden, svenska i `sv.po`
