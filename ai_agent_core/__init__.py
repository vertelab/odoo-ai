# -*- coding: utf-8 -*-
"""ai_agent_core — standalone AI agent engine for Odoo."""
from . import hooks
from .hooks import post_init_hook_personal_memory
from .hooks import pre_init_hook_check_conflicts
# Models and controllers require Odoo runtime; skip for standalone testing
try:
    from . import controllers
    from . import models
    from . import wizards
except (ImportError, AssertionError):
    pass
