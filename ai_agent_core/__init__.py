# -*- coding: utf-8 -*-
"""ai_agent_core — standalone AI agent engine for Odoo."""
# Models and controllers require Odoo runtime; skip for standalone testing
try:
    from . import controllers
    from . import models
except (ImportError, AssertionError):
    pass
