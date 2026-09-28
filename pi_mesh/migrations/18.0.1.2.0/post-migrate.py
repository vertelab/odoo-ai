# -*- coding: utf-8 -*-
"""
Migration: säkerställ webhook-token.

VARFÖR: `post_init_hook` körs bara vid INSTALLATION, inte vid
uppgradering. Modulen installerades först utan hooken, så token blev
aldrig satt — och webhooken skulle svara 401 för varje agent.

Samma läxa som saltstack.alert: en nyckel som bara skapas i en hook
finns inte på system som uppgraderats i stället för installerats.
"""

import logging
import secrets

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    from odoo import api, SUPERUSER_ID

    env = api.Environment(cr, SUPERUSER_ID, {})
    params = env['ir.config_parameter'].sudo()

    if not params.get_param('pi_mesh.webhook_token'):
        token = secrets.token_urlsafe(32)
        params.set_param('pi_mesh.webhook_token', token)
        _logger.info('pi_mesh migration: webhook-token genererad')

    if not params.get_param('pi_mesh.webhook_enabled'):
        params.set_param('pi_mesh.webhook_enabled', 'True')
        _logger.info('pi_mesh migration: webhook aktiverad')
