# odoo-ai

When installing the Python dependencies/requirements, one can run into issues with the pre-installed Python packages on Ubuntu 24.04, such as the one below.
```
ERROR: Cannot uninstall typing_extensions 4.10.0, RECORD file not found. Hint: The package was installed by debian.
```
To fix this issue, the easiest way is to run the command below in the project/repository directory.
```
sudo pip3 install -r requirements.txt --ignore-installed --break-system-packages
```


## Definition of Done — AI-bryggmoduler (`_ai`)

Varje domän-`_ai`-modul (t.ex. `marketing_ai`, `strategy_ai`, `social_ai`)
måste uppfylla följande innan den räknas som klar:

1. **Manifest**: `depends` inkluderar `ai_agent_core` (INTE legacy `ai_agent`)
2. **AI-förmågor som data-XML**: coworkers (`ai.coworker`) + skills (`ai.skill`)
   i `data/`, aldrig Python-create (undantag: thin post_init_hook-moduler)
3. **Körning**: `coworker.run()` / `coworker.powerbox()` — aldrig `ai.quest`
4. **Inga `ai.quest`/`ai.agent`-referenser** i modellkod (grep ska vara tom)
5. **Odoo 18-kompatibla views**: `list` (inte `tree`), `invisible` (inte
   `attrs`/`states`), inga borttagna ir.cron-fält (`numbercall`, `description`)
6. **Modulrot `__init__.py`** finns (`from . import models`)
7. **AI-förmågor hör ENBART hemma i `_ai`-moduler** — domän-core är ren
   (ingen AI-kod, inga AI-fält, inga AI-beroenden)

### Checklista för ny domänbrygga

- [ ] Modulrot `__init__.py` (`from . import models`)
- [ ] Manifest: depends `['<domän_core>', 'ai_agent_core']`
- [ ] `data/<modul>_coworkers.xml` — coworkers (name, description, status,
      model_ids, filter_domain, injection_level, hitl_threshold,
      orchestration_mode, example_prompts, skill_ids)
- [ ] `data/<modul>_skills.xml` — skills (giltiga kategorier:
      accounting/development/infrastructure/analysis/communication/research/general)
- [ ] Bridge-modell: `_inherit = 'ai.coworker'` vid behov (domänfält/UI)
- [ ] Views Odoo 18-kompatibla
- [ ] `grep -rn "ai.quest\|ai.agent" <modul>/` → inga träffar
- [ ] Installationstest mot test-DB (checkmodule -d <db> -m <modul>)
- [ ] README-uppdatering i domänrepon

Referens: `AUDIT-ai-bridge-modules.md` (inventering av alla `_ai`-moduler).

## Porteringsstatus

18.0 är baseline. 19.0/20.0 visar om p-filen har ett villkor för utgåvan
— inte om grenen finns. Blockerare är `depends` som saknas i 20.0
(kärna + alla 20.0-grenar).

| Modul | Beskrivning | Manifest | p-fil | 19.0 | 20.0 | Blockerare |
|---|---|---|---|---|---|---|
| agent_pi | Distributed Pi Coding Agent orchestration via NATS. | 1.0.0 | 1 | — | — | — |
| ai_agent | AI Agent orchestration | 1.0 | 76 | — | — | `web_widget_mermaid_field` |
| ai_agent_context | Injects record data and chatter history as context into A... | 1.1.0 | 1 | — | — | — |
| ai_agent_core | AI Agent Core — Org-stomme, heartbeat, onboarding, task-s... | 1.303 | 12 | ✓ | ✓ | — |
| ai_agent_core_academic | Academic paper writing pipeline — 8-agent team for resear... | 1.0.0 | 1 | — | — | — |
| ai_agent_core_strategy | 21 AI skills, 5 agents, 3 quests for business strategy — ... | 1.0.0 | 1 | — | — | — |
| ai_agent_hr | Agent Orchestration HR | 0.3 | 10 | — | — | — |
| ai_agent_mcp | Adds MCP Power to AI Agent and AI Quests | 1.0 | 17 | — | ✓ | `fastapi` |
| ai_agent_pgvector | Ao Orchestration and memory using pg_vector | 1.0.3 | 18 | — | ✓ | — |
| ai_agent_transcript | Powerbox-style AI interaction with transcript/context inj... | 1.0.7 | 1 | — | — | — |
| ai_agent_trend | Agent for analysing trends and create material for blogs ... | 0.3 | 14 | — | ✓ | — |
| ai_agent_zabbix | Zabbix integration for AI agent monitoring. | 1.0.0 | 1 | — | — | — |
| ai_core | Provider abstraction, model discovery, rate limiting, tok... | 1.0.0 | 1 | — | — | — |
| ai_mail_e_avrop | Mailbox for AI. | 1.0.1 | 1 | — | — | — |
| ai_onboarding | Onboardingskurs: co-pilot, coworker och AI i vardagen | 1.0.0 | 0 | — | — | — |
| ai_pi_mcp | Standalone MCP streamable-HTTP server for Pi system devel... | 1.0.0 | 1 | — | — | — |
| gamification_ai | Connect gamification (badges, challenges) with AI persona... | 1.0.0 | 1 | — | — | — |
| pi_mesh | Historik och överblick för Pi-agenternas mesh | 1.6.0 | 1 | — | — | — |
