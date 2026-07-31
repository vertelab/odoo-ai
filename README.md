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
