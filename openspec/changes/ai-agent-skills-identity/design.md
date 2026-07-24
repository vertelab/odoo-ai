# Design: Kaizen, ONBOARD & Identity Learning

## Hole 1: Kaizen — Per-Quest Weekly Self-Review

### Decisions

**D1 — Per-quest kaizen (Alternativ A)**
Varje quest får en egen kaizen-review. En cron väcker questen, agenten analyserar questens egna sessioner från senaste veckan, och föreslår förbättringar.

**D2 — Autonomi-nivå 2: Föreslå + kräv godkännande**
Kaizen får INTE göra ändringar själv. Den analyserar, rapporterar, och föreslår — men alla ändringar (identity, skills, modell-val, temperatur, beskrivning) kräver mänskligt godkännande.

### Arkitektur

```
┌──────────────────────────────────────────────────────────────────────────┐
│                     KAIZEN-LOOP (per quest, weekly)                       │
│                                                                          │
│  Cron (söndag 03:00) → för varje aktiv quest:                           │
│                                                                          │
│  ┌──────────────────────────────────────────────────────────────────┐    │
│  │  FÖRBERED DATA                                                   │    │
│  │                                                                  │    │
│  │  Hämta senaste veckans:                                          │    │
│  │    • sessioner (antal, status, duration)                          │    │
│  │    • tokens (input, output, systemtokens, kostnad)                │    │
│  │    • errors (timeout, tool crash, LLM refuse)                     │    │
│  │    • feedback ("Förbättra:"-kommandon)                            │    │
│  │    • cap events (varningar, överskridanden)                       │    │
│  │    • jämför med föregående vecka (trend)                          │    │
│  └──────────────────────────────────────────────────────────────────┘    │
│                              │                                           │
│                              ▼                                           │
│  ┌──────────────────────────────────────────────────────────────────┐    │
│  │  ANALYSERA                                                       │    │
│  │                                                                  │
│  │  Agent (billig modell, t.ex. DeepSeek) analyserar:               │    │
│  │                                                                  │    │
│  │  Prompt:                                                          │    │
│  │  "You are analyzing the performance of AI quest '{quest.name}'. │    │
│  │   Here is last week's data. Identify:                             │    │
│  │   1. Patterns in errors — is there a common cause?                │    │
│  │   2. Cost anomalies — any sessions unusually expensive?           │    │
│  │   3. Feedback themes — what are users asking to improve?          │    │
│  │   4. Optimization opportunities — cheaper model? Better prompt?   │    │
│  │   5. Skill gaps — would a new skill reduce errors?                │    │
│  │                                                                  │    │
│  │   Return JSON: [{finding, severity, recommendation, evidence}]"   │    │
│  └──────────────────────────────────────────────────────────────────┘    │
│                              │                                           │
│                              ▼                                           │
│  ┌──────────────────────────────────────────────────────────────────┐    │
│  │  RAPPORTERA                                                      │    │
│  │                                                                  │    │
│  │  Generera Kaizen-rapport:                                         │    │
│  │                                                                  │    │
│  │  ┌────────────────────────────────────────────────────────────┐  │    │
│  │  │  📊 Kaizen-rapport: Bokföringsanalys — vecka 30            │  │    │
│  │  │                                                            │  │    │
│  │  │  📈 Översikt                                               │  │    │
│  │  │    47 sessioner (↓3%), 892K systemtokens (↑12%)            │  │    │
│  │  │    Genomsnitt/session: 19K tokens, 3.2s svarstid           │  │    │
│  │  │                                                            │  │    │
│  │  │  ⚠️  Hög prioritet                                         │  │    │
│  │  │    23% av felen är moms-relaterade                          │  │    │
│  │  │    → Rekommendation: lägg till skill 'Swedish VAT'         │  │    │
│  │  │    → Evidens: 8 av 35 fel innehåller "moms" eller "VAT"   │  │    │
│  │  │    [Lägg till skill] [Ignorera]                            │  │    │
│  │  │                                                            │  │    │
│  │  │  💡 Förbättringsförslag                                    │  │    │
│  │  │    Kostnad ↑12% — byt från Claude till GPT-4o?             │  │    │
│  │  │    → Besparing: ~1200 SEK/månad vid samma kvalitet         │  │    │
│  │  │    [Byt modell] [Ignorera]                                 │  │    │
│  │  │                                                            │  │    │
│  │  │  ✅ Förra veckans åtgärder                                  │  │    │
│  │  │    [Godkänt] Lade till skill 'Swedish Accounting'          │  │    │
│  │  │    [Avböjt] Byt till DeepSeek (kvalitetsrisk)              │  │    │
│  │  └────────────────────────────────────────────────────────────┘  │    │
│  │                                                                  │    │
│  │  Rapporten sparas som ai.kaizen.report + postas i questens      │    │
│  │  discuss-kanal. Godkända åtgärder appliceras automatiskt.       │    │
│  └──────────────────────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────────────────────┘
```

### Data model

```python
class AIKaizenReport(models.Model):
    _name = 'ai.kaizen.report'
    _description = 'Kaizen Weekly Report'
    _order = 'week_start desc'

    quest_id = fields.Many2one('ai.quest', required=True, ondelete='cascade')
    week_start = fields.Date('Week Starting')
    week_end = fields.Date('Week Ending')

    # Metrics
    session_count = fields.Integer('Sessions')
    total_sys_tokens = fields.Integer('Systemtokens')
    error_count = fields.Integer('Errors')
    feedback_count = fields.Integer('Feedback Items')
    avg_response_time = fields.Float('Avg Response Time (s)')

    # Previous week comparison
    session_trend = fields.Float('% Change')
    cost_trend = fields.Float('% Change')
    error_trend = fields.Float('% Change')

    # Findings (JSON)
    findings_json = fields.Text('Findings',
        help='JSON: [{finding, severity, recommendation, evidence, action}]')

    # Actions taken
    actions_json = fields.Text('Actions Taken',
        help='JSON: [{finding_id, approved, applied, result}]')

    # Report text (rendered markdown)
    report_text = fields.Text('Report')


class AIKaizenFinding(models.Model):
    _name = 'ai.kaizen.finding'
    _description = 'Kaizen Finding'

    report_id = fields.Many2one('ai.kaizen.report', required=True, ondelete='cascade')
    severity = fields.Selection([
        ('low', '💡 Förslag'), ('medium', '⚠️ Varning'), ('high', '🔴 Kritisk'),
    ])
    category = fields.Selection([
        ('cost', 'Kostnad'), ('error', 'Fel'), ('performance', 'Prestanda'),
        ('skill_gap', 'Kompetensgap'), ('feedback', 'Feedback'),
    ])
    finding = fields.Text('Finding')
    recommendation = fields.Text('Recommendation')
    evidence = fields.Text('Evidence')
    status = fields.Selection([
        ('pending', 'Pending'), ('approved', 'Approved'),
        ('rejected', 'Rejected'), ('applied', 'Applied'),
    ], default='pending')
```

---

## Hole 2: ONBOARD — Mine the Instance for Quest Candidates

### Decisions

**D3 — Minar egna Odoo-instansen**
ONBOARD scannar den egna Odoo-databasen efter quest-kandidater. Inga externa kodbaser.

**D4 — Fynd presenteras vid kaizen-tillfället**
ONBOARD körs flera gånger dagligen, men FYNDEN lagras och presenteras först vid nästa kaizen-review. Inget skapas automatiskt.

**D5 — Verktygslåda: notifiera, skapa action, integrera moduler**
Om helpdesk/projekt/avvikelse finns installerade → använd dem. Annars: discuss-notifikation + ir.actions.server.

**D6 — Körs ofta, konfigurerbart**
Default: var 4:e timme. Admin kan ändra frekvens.

### Arkitektur

```
┌──────────────────────────────────────────────────────────────────────────┐
│                     ONBOARD — SCANNA EFTER QUEST-KANDIDATER               │
│                                                                          │
│  Cron (var 4:e timme) →                                                   │
│                                                                          │
│  ┌──────────────────────────────────────────────────────────────────┐    │
│  │  SCAN-KÄLLOR                                                     │    │
│  │                                                                  │    │
│  │  1. Data quality — Odoo-modeller                                 │    │
│  │     • search_count på nyckelmodeller med saknade fält            │    │
│  │     • gamla/inkonsistenta poster                                 │    │
│  │     • Ex: "342 partners without email"                           │    │
│  │                                                                  │    │
│  │  2. Repetitiva tasks — ir.cron + ir.actions.server               │    │
│  │     • Vilka server actions körs ofta manuellt?                   │    │
│  │     • Vilka rapporter begärs >10 ggr/vecka?                      │    │
│  │     • Ex: "Report X requested 37 times this week"                │    │
│  │                                                                  │    │
│  │  3. Module gaps — ir.module.module                                │    │
│  │     • Installerade moduler som inte används → onboarding quests  │    │
│  │     • Saknade moduler som skulle lösa kända problem              │    │
│  │                                                                  │    │
│  │  4. Error patterns — ai.quest.session (status=error)              │    │
│  │     • Återkommande feltyper → "skapa en quest för att hantera X" │    │
│  │                                                                  │    │
│  │  5. Integration gaps (om moduler finns)                          │    │
│  │     • helpdesk: återkommande ticket-ämnen → quest-kandidat       │    │
│  │     • project: tasks utan automatiserad workflow                 │    │
│  │     • mgmtsystem: avvikelser som kan automatgranskas             │    │
│  └──────────────────────────────────────────────────────────────────┘    │
│                              │                                           │
│                              ▼                                           │
│  ┌──────────────────────────────────────────────────────────────────┐    │
│  │  SPARA FYND                                                      │    │
│  │                                                                  │    │
│  │  Varje fynd → ai.onboard.candidate:                              │    │
│  │    source: "data_quality" | "repetitive_task" | "module_gap" ... │    │
│  │    description: "342 partners utan email"                         │    │
│  │    suggested_quest_type: "monitoring" | "automation" | "cleanup" │    │
│  │    confidence: 0.0–1.0                                            │    │
│  │    discovered_at: datetime                                        │    │
│  │    status: "new" (visas vid nästa kaizen)                         │    │
│  └──────────────────────────────────────────────────────────────────┘    │
│                                                                          │
│  ┌──────────────────────────────────────────────────────────────────┐    │
│  │  VID KAIZEN: PRESENTERA FYND                                     │    │
│  │                                                                  │    │
│  │  Kaizen-rapporten inkluderar en "🆕 Upptäckta möjligheter"-     │    │
│  │  sektion med ONBOARD-fynd från senaste veckan.                   │    │
│  │                                                                  │    │
│  │  För varje fynd:                                                  │    │
│  │    [Skapa quest] [Skapa ticket] [Skapa action] [Ignorera]        │    │
│  │                                                                  │    │
│  │  Om fyndet kopplas till helpdesk/projekt:                        │    │
│  │    → skapa ticket/task istället för quest                        │    │
│  └──────────────────────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────────────────────┘
```

### Data model

```python
class AIOnboardCandidate(models.Model):
    _name = 'ai.onboard.candidate'
    _description = 'ONBOARD Discovery'
    _order = 'confidence desc, discovered_at desc'

    source = fields.Selection([
        ('data_quality', 'Data Quality'),
        ('repetitive_task', 'Repetitive Task'),
        ('module_gap', 'Module Gap'),
        ('error_pattern', 'Error Pattern'),
        ('integration', 'Integration Gap'),
    ], required=True)
    description = fields.Text('Description', required=True)
    suggested_quest_type = fields.Selection([
        ('monitoring', 'Monitoring'),
        ('automation', 'Automation'),
        ('cleanup', 'Data Cleanup'),
        ('report', 'Report Generation'),
        ('integration', 'Integration'),
    ])
    confidence = fields.Float('Confidence', default=0.5)
    evidence = fields.Text('Evidence')
    source_module = fields.Char('Source Module')
    record_count = fields.Integer('Affected Records', default=0)

    discovered_at = fields.Datetime(default=lambda self: fields.Datetime.now())
    status = fields.Selection([
        ('new', 'New'), ('presented', 'Presented at Kaizen'),
        ('created_quest', 'Quest Created'), ('created_ticket', 'Ticket Created'),
        ('created_action', 'Action Created'), ('ignored', 'Ignored'),
    ], default='new')

    # Result if acted upon
    resulting_quest_id = fields.Many2one('ai.quest', string='Created Quest')
    resulting_ticket_id = fields.Integer('Ticket ID')
    presented_at_kaizen = fields.Many2one('ai.kaizen.report')
```

---

## Hole 3: Identity Learning — Personal AI Companion

### Decisions

**D7 — Automatisk uppdatering av user_model**
Agenten observerar interaktioner och uppdaterar `ai.identity.user_model` automatiskt. Användaren kan gå in och justera manuellt om det inte stämmer.

**D8 — Både explicit och implicit /learn**
- Explicit: användaren skriver `/learn jag föredrar korta svar`
- Implicit: agenten observerar och uppdaterar tyst (men allt syns i user_model)

**D9 — Personlig AI-companion som system-inställning**
En boolean `ai.personal_companion_enabled` på `res.config.settings`. När påslagen: en personlig `ai.quest` skapas per användare, på samma sätt som `res.users` automatiskt har en `res.partner`.

**D10 — Kopierad identity som utvecklas över tid**
Den personliga questen får en **kopia** av en vald identity template. Kopian lever sitt eget liv — den utvecklas baserat på användarens interaktioner, oberoende av originalmallen. Samma mönster som `ai.quest.skill` (quest-specifik fork av shared skill).

### Arkitektur

```
┌──────────────────────────────────────────────────────────────────────────┐
│                     PERSONLIG AI-COMPANION                               │
│                                                                          │
│  res.config.settings                                                    │
│  ├── ai_personal_companion_enabled (bool)                                │
│  └── ai_personal_companion_identity_id (m2o → ai.identity template)      │
│                                                                          │
│  När påslagen + spara:                                                   │
│                                                                          │
│  ┌──────────────────────────────────────────────────────────────────┐    │
│  │  PER ANVÄNDARE                                                   │    │
│  │                                                                  │    │
│  │  res.users (1) ──── arv ──── res.partner (1)                     │    │
│  │       │                                                          │    │
│  │       └─── arv ──── ai.quest (1 per user)                        │    │
│  │                      │                                           │    │
│  │                      ├── name: "{user.name}'s AI Companion"       │    │
│  │                      ├── init_type: "chat"                        │    │
│  │                      ├── user_id: user                            │    │
│  │                      ├── show_in_chat: True                       │    │
│  │                      │                                           │    │
│  │                      └── identity_id: → KOPIA av template        │    │
│  │                           │                                      │    │
│  │                           ├── personality: "..." (från template)  │    │
│  │                           ├── style: "..."                        │    │
│  │                           ├── values: "..."                       │    │
│  │                           ├── boundaries: "..."                   │    │
│  │                           └── user_model: "" (börjar tomt)        │    │
│  │                                                                  │    │
│  │  Kopian lever sitt eget liv — template-ändringar påverkar INTE   │    │
│  │  redan skapade personliga kopior.                                 │    │
│  └──────────────────────────────────────────────────────────────────┘    │
│                                                                          │
│  ┌──────────────────────────────────────────────────────────────────┐    │
│  │  /learn — EXPLICIT INLÄRNING                                     │    │
│  │                                                                  │    │
│  │  Användaren i chatten:                                            │    │
│  │                                                                  │    │
│  │  /learn jag föredrar korta svar, max 3 meningar                  │    │
│  │       │                                                          │    │
│  │       ▼                                                          │    │
│  │  identity.style += "\n- Föredrar korta svar, max 3 meningar"     │    │
│  │  → Svar: "Jag har noterat att du föredrar korta svar."           │    │
│  │                                                                  │    │
│  │  /learn jag jobbar med svensk bokföring, fokusera på BAS-konton  │    │
│  │       │                                                          │    │
│  │       ▼                                                          │    │
│  │  identity.user_model += "\n- Arbetar med svensk bokföring"       │    │
│  │  identity.user_model += "\n- Fokus: BAS-konton"                  │    │
│  └──────────────────────────────────────────────────────────────────┘    │
│                                                                          │
│  ┌──────────────────────────────────────────────────────────────────┐    │
│  │  IMPLICIT INLÄRNING (efter varje session)                        │    │
│  │                                                                  │    │
│  │  Efter assistant-svar → lättvikt extraktion (samma som idag):    │    │
│  │                                                                  │    │
│  │  "Extrahera 1-3 fakta om användaren från denna konversation."     │    │
│  │                                                                  │    │
│  │  → [{fact: "Användaren ber alltid om CSV-export",                 │    │
│  │      category: "preference", importance: "medium"}, ...]          │    │
│  │                                                                  │    │
│  │  Facts lagras som ai.memory + uppdaterar identity.user_model:    │    │
│  │                                                                  │    │
│  │  "Användaren ber alltid om CSV-export"                            │    │
│  │  → identity.user_model += "\n- Föredrar CSV-export"              │    │
│  └──────────────────────────────────────────────────────────────────┘    │
│                                                                          │
│  ┌──────────────────────────────────────────────────────────────────┐    │
│  │  ANVÄNDAREN KAN ALLTID JUSTERA                                   │    │
│  │                                                                  │    │
│  │  identity.user_model är ett textfält som användaren kan redigera │    │
│  │  fritt. All automatik är förslag — användaren äger sin data.     │    │
│  └──────────────────────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────────────────────┘
```

### Data model changes

```python
# res.users — lägg till personal companion relation
class ResUsers(models.Model):
    _inherit = 'res.users'

    personal_quest_id = fields.Many2one(
        'ai.quest', string='AI Companion',
        help='Personal AI quest for this user. Created automatically '
             'when ai_personal_companion_enabled is activated.')

# res.config.settings — system-inställning
class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    ai_personal_companion_enabled = fields.Boolean(
        'Personal AI Companion',
        config_parameter='ai_agent_core.personal_companion_enabled',
        help='Create a personal AI quest for each user')

    ai_personal_companion_identity_id = fields.Many2one(
        'ai.identity', 'Default Identity Template',
        config_parameter='ai_agent_core.personal_companion_identity_id',
        help='Identity template to copy for new personal companions')

# ai.identity — lägg till kopierings-metod
class AIIdentity(models.Model):
    _inherit = 'ai.identity'

    def copy_for_user(self, user):
        """Create a personal copy of this identity for a specific user."""
        copy = self.copy({
            'name': f"{self.name} — {user.name}",
            'scope': 'personal',
        })
        return copy
```

---

## Sammanfattning — alla tre hål

| Hål | Kärnbeslut | Nya modeller |
|-----|-----------|-------------|
| 1. Kaizen | Per-quest, autonomi-nivå 2 (föreslå + godkänn) | `ai.kaizen.report`, `ai.kaizen.finding` |
| 2. ONBOARD | Minar egen Odoo, fynd vid kaizen, verktygslåda | `ai.onboard.candidate` |
| 3. Identity | Automatisk user_model, /learn, personal companion | `res.users.personal_quest_id`, identity.copy_for_user() |

### Implementation ordning

1. **Identity learning** (Hål 3) — ~1 dag, lägst risk, bygger på befintlig memory extraction
2. **Kaizen** (Hål 1) — ~3 dagar, bygger på identity + session data
3. **ONBOARD** (Hål 2) — ~3 dagar, bygger på kaizen (presenteras där)
