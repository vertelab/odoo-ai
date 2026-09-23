# -*- coding: utf-8 -*-
"""ai_agent_core — standalone AI agent engine for Odoo."""
from . import hooks
from .hooks import (post_init_hook, post_init_hook_personal_memory,
                     pre_init_hook_check_conflicts)
# Models and controllers require Odoo runtime
import logging
_logger = logging.getLogger(__name__)

# /ai/** ska alltid svara JSON — Odoo renderar annars HTTPException (UserError ->
# BadRequest) som Werkzeugs HTML-sida för type="http"-routes (2026-09-20).
try:
    from . import http_guard
    http_guard.install()
except Exception as e:
    _logger.error("Failed to install ai_agent_core http_guard: %s", e, exc_info=True)
try:
    from . import controllers
except Exception as e:
    _logger.error('Failed to import ai_agent_core controllers: %s', e, exc_info=True)
try:
    from . import models
except Exception as e:
    _logger.error('Failed to import ai_agent_core models: %s', e, exc_info=True)
try:
    from . import wizards
except Exception as e:
    _logger.error('Failed to import ai_agent_core wizards: %s', e, exc_info=True)
