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

# Kanal-registreringen (channel-adapter-wiring 4.5).
#
# `channel_registry` är process-global och töms vid varje processstart, så
# kanalerna måste registreras när modulen importeras — inte bara i
# `post_init_hook` (som körs en gång vid installation).
#
# Fällan (mätt 2026-11-09): registreringen låg BARA i post_init_hook. Efter
# en omstart var registret tomt, `process_item()` gav 'no_processor' och
# `dispose()` anropades aldrig — mail-svar skickades aldrig i drift, tyst.
# Att lägga anropet i `core/channels.py` på modulnivå räckte inte heller:
# alla importer av `core.*` i modellen är LATA (inuti funktioner), så
# filen importerades aldrig vid uppstart.
try:
    from .core.channels import register_chat_channel, register_mail_channel
    register_mail_channel()
    register_chat_channel()
except Exception as e:
    _logger.error(
        'Failed to register ai_agent_core channels: %s', e, exc_info=True)
