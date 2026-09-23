# -*- coding: utf-8 -*-
"""Skydd mot ir.config_parameter-läckage mellan tester.

VARFÖR (FYND 2026-09-23):

`ir.config_parameter.get_param()` är `@ormcache('key')` — värdet cachas i
registryt. `TransactionCase` rullar tillbaka DB-transaktionen efter varje
test, men **cachen rensas inte**. Ett test som skriver en parameter via
`set_values()`/`set_param()` lämnar därför kvar sitt värde i cachen, och
nästa testklass läser det gamla värdet trots att raden är borta ur DB.

Symptomet: `test_builtin_fallback_removal` och `test_explicit_agent_tools`
såg `odoo_unlink`/`okf_search` i en tom coworkers verktygslista — verktyg
som bara `agent_odoo_business` har, och som bara kom in för att
`ai_agent_core.default_tool_ids` läckte från ett tidigare test.

Använd `ConfigParamGuardedCase` i stället för `common.TransactionCase` i
testklasser som rör ai_agent_core-parametrar. Den rensar cachen i
tearDown så varje test startar med DB:ns (rullade tillbaka) sanning.
"""

from odoo.tests import common

# Parametrar som ai_agent_core skriver och som påverkar verktygsval,
# modellval och minnesbeteende. Rensas alltid efter test.
_GUARDED_KEYS = (
    'ai_agent_core.default_tool_ids',
    'ai_agent_core.default_model_id',
    'ai_agent_core.default_provider_id',
    'ai_agent_core.google_api_key',
)


class ConfigParamGuardedCase(common.TransactionCase):
    """TransactionCase som rensar ir.config_parameter-cachen i tearDown."""

    def tearDown(self):
        super().tearDown()
        try:
            self.env.registry.clear_cache()
        except Exception:
            # Äldre API — fall tillbaka på att invalidatera parametrarna.
            for key in _GUARDED_KEYS:
                self.env['ir.config_parameter'].sudo().invalidate_model(
                    ['key', 'value'])
