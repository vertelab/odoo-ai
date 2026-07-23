# -*- coding: utf-8 -*-
# Core models — always loaded
from . import ai_identity
from . import ai_skill
from . import ai_session

# Conditional: only load if ai_agent module is available
try:
    from odoo.addons.ai_agent import models as _ai_agent_models
    from . import ai_quest
    from . import discuss_channel
except (ImportError, ModuleNotFoundError):
    pass  # ai_agent not installed — these integrations are skipped
