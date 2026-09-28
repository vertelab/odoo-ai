"""agent-model-resolution: ge agenter utan modell en default.

Mätt 2026-09-18: 20 av 21 agenter saknade `model_id`, och parametern
`ai_agent_core.default_model_id` existerade inte. Kedjan
ai.coworker → agent → ai.model → ai.provider är den enda vägen till en
LLM, så ingen av dem kunde köra.

Parametern sätts först (samma logik som post_init), därefter reparationen.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return

    from odoo import api, SUPERUSER_ID

    env = api.Environment(cr, SUPERUSER_ID, {})

    # 1. Säkerställ parametern
    from odoo.addons.ai_agent_core.hooks import _ensure_default_model
    _ensure_default_model(env)

    # 2. Reparera agenterna
    repaired = env['ai.coworker']._repair_missing_agent_models()
    _logger.info(
        'agent-model-resolution: %d agenter fick en modell', repaired)
