# -*- coding: utf-8 -*-
"""ai_agent_core — standalone AI agent engine for Odoo."""
from . import hooks
# Models and controllers require Odoo runtime; skip for standalone testing
try:
    from . import controllers
    from . import models
except (ImportError, AssertionError):
    pass
