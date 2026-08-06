# Plan: `/odoo-screenshot <module>` — Pi-kommando för skärmdumpar av Odoo-vyer

## Context

Användaren vill ha ett Pi-kommando `/odoo-screenshot <module>` som använder `agent-browser` (Rust CLI för Chrome DevTools Protocol) för att ta skärmdumpar av **samtliga vyer** i en Odoo-modul.

**Varför:** För att snabbt kunna granska hur alla vyer i en modul ser ut, fånga JS-fel, och underlätta kvalitetsgranskning — utan att behöva klicka sig igenom Odoo manuellt.

## Approach

### Arkitektur

```
/odoo-screenshot <module> [options]
  │
  ├─ 1. Upptäckt: odoo_scan + modulens XML-filer → lista av (view_name, view_type, action)
  ├─ 2. Agent-browser: för varje vy → open Odoo, navigera, screenshot
  └─ 3. Output: screenshots/<module>/<view_name>.png + errors.log
```

Rekommenderad implementation: **Pi-extension (TypeScript)** som registrerar en slash command. Detta ger:
- Direkt integration med Pi:s kommandosystem
- Tillgång till `odoo_scan`-verktyget för att upptäcka vyer
- Möjlighet att anropa `agent-browser` via bash
- Status-uppdateringar till användaren under körning

Alternativ: En **Pi-skill** med ett wrapper-script — enklare men mindre integrerat.

### Flöde

1. **Parsa argument:** `module` (obligatoriskt), `--url` (Odoo URL), `--state` (auth state-fil), `--headed` (visa browser-fönster), `--only` (filtrera på vytyp)
2. **Upptäck vyer:** Kör `odoo_scan` i modulens katalog → extrahera alla `view_xml_ids` + vy-typ
3. **Bygg Odoo-menyn:** Mappa varje XML ID till en Odoo action URL
4. **Agent-browser loop:** För varje vy:
   - `agent-browser open <odoo_url>` + ev. inloggning
   - `agent-browser wait --load networkidle`
   - `agent-browser screenshot --full <path>`
   - `agent-browser console` → logga JS-fel
5. **Sammanställ resultat:** Antal lyckade/misslyckade skärmdumpar, error-logg

### Output-struktur

Skärmdumparna sparas direkt i modulens `static/description/`-katalog:

```
<module>/static/description/
├── screenshot_form_<xml_id>.png
├── screenshot_tree_<xml_id>.png
├── screenshot_kanban_<xml_id>.png
├── screenshot_search_<xml_id>.png
├── ...
└── screenshot_errors.log        # JS-fel per vy
```

**Namnkonvention:** `screenshot_<type>_<xml_id>.png` där `<xml_id>` är vy:ns fullständiga XML ID (t.ex. `view_partner_form` → `screenshot_form_view_partner_form.png`). Detta gör varje fil unikt identifierbar och spårbar tillbaka till vy-definitionen.

Detta följer Odoos konvention — `static/description/` används för modulens dokumentationsbilder (t.ex. `icon.png`, `banner.png`). Skärmdumparna blir en del av modulens assets och kan versioneras i git tillsammans med modulen.

## Files to modify/create

| Fil | Åtgärd |
|-----|--------|
| `/usr/local/share/pi/skills/odoo-screenshot/SKILL.md` | **Ny** — Pi-skill med instruktioner för agent-browser workflow |
| `/usr/local/share/pi/skills/odoo-screenshot/scripts/screenshot-module.sh` | **Ny** — Bash-script som orkestrerar agent-browser för en modul |

Alternativt (Pi-extension):

| Fil | Åtgärd |
|-----|--------|
| `/usr/local/share/pi/extensions/odoo-screenshot.ts` | **Ny** — Pi-extension som registrerar `/odoo-screenshot` slash command |

## Reuse

| Befintlig komponent | Sökväg | Hur den återanvänds |
|---|---|---|
| `agent-browser` skill | `/usr/local/share/pi/skills/agent-browser/SKILL.md` | Browser-automation: open, wait, screenshot, console, errors |
| `odoo_scan` tool | `odoo-unified` extension | Upptäcker alla moduler och deras `view_xml_ids` |
| `odoo_find_xmlid` tool | `odoo-unified` extension | Hittar XML ID:n för views och actions |
| `odoo-unified` skill | `/usr/local/share/pi/skills/odoo-unified/` | Odoo-kontext, module-struktur, conventions |
| `~/.pi/agent/settings.json` | Pi config | Konfiguration för Odoo URL, auth state |

## Steps

- [ ] 1. Skapa `/usr/local/share/pi/skills/odoo-screenshot/` katalogstruktur
- [ ] 2. Skriv `SKILL.md` — instruktioner för hur Pi-agenten ska använda agent-browser för att skärmdumpa Odoo-vyer
- [ ] 3. Skriv `scripts/screenshot-module.sh` — bash-script som:
  - Acceptar `<module> [--url URL] [--state STATE_FILE] [--headed] [--only TYPE]`
  - Kör `odoo_scan` för att hitta vyer
  - Loopar över vyer och kör `agent-browser`-kommandon
  - Hanterar auth via `--state` (agent-browser state save/load)
  - Samlar JS-fel från `agent-browser console`
  - Sparar skärmdumpar i `<module>/static/description/screenshot_<type>_<xml_id>.png` + `screenshot_errors.log`
- [ ] 4. (Valfritt) Skapa Pi-extension `odoo-screenshot.ts` som registrerar `/odoo-screenshot` slash command
- [ ] 5. Dokumentera användning i SKILL.md: `--help`, exempel, felsökning

## Script-design (`screenshot-module.sh`)

```bash
#!/bin/bash
# /odoo-screenshot <module> — screenshot all views in an Odoo module
# Output: <module>/static/description/screenshot_<type>_<model>.png
set -euo pipefail

MODULE="$1"; shift
ODOO_URL="${ODOO_URL:-https://odoo.example.com}"
AUTH_STATE="${AUTH_STATE:-}"
OUTPUT_DIR="${MODULE}/static/description"

mkdir -p "$OUTPUT_DIR"
echo "# Odoo Screenshot: $MODULE" > "$OUTPUT_DIR/screenshot_errors.log"

# 1. Upptäck vyer via odoo_scan → lista av (model, view_type, xml_id)
# 2. För varje vy: navigera → screenshot → fånga errors
#    Filnamn: screenshot_<type>_<xml_id>.png (inkluderar XML ID för spårbarhet)
# 3. Sammanställ
```

## Verifiering

1. **Grundläggande:** Kör `/odoo-screenshot module_catalog` — ska producera en skärmdump per vy i `module_catalog/static/description/`
2. **Auth:** Testa med `--state ./odoo-auth.json` — ska logga in automatiskt
3. **Felsökning:** Testa med en modul som har JS-fel — `errors.log` ska innehålla felmeddelanden
4. **Filtrering:** Kör `--only form` — ska bara ta skärmdumpar av formulär-vyer
5. **Headed:** Kör `--headed` — browser-fönstret ska synas (för debugging)
6. **Edge cases:** Tom modul (inga vyer), modul med trasiga vyer, nätverksproblem mot Odoo
