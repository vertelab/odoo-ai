# -*- coding: utf-8 -*-
"""
Installationskrok: skapa webhook-token.

VARFÖR: en token som någon måste komma ihåg att sätta blir aldrig satt.
Samma läxa som saltstack.alert — nyckeln genereras vid installation och
kan läsas i Inställningar. Utan den svarar webhooken 401 och agenterna
skulle behöva en hemlighet som ingen delat ut.
"""

import logging
import secrets

_logger = logging.getLogger(__name__)


def post_init_hook(env):
    """Generera webhook-token om den saknas."""
    params = env['ir.config_parameter'].sudo()
    if not params.get_param('pi_mesh.webhook_token'):
        token = secrets.token_urlsafe(32)
        params.set_param('pi_mesh.webhook_token', token)
        _logger.info('pi_mesh: webhook-token genererad')
    params.set_param('pi_mesh.webhook_enabled', 'True')
