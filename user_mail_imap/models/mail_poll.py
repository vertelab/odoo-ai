# -*- coding: utf-8 -*-
"""Konkret modell som ir.cron pekar på för IMAP-pollning.

user.mail.imap är abstract — ir.cron behöver en konkret modell. All logik
ligger i user.mail.imap.action_poll_all(); denna modell är bara en
cron-anknytning.
"""

import logging

from odoo import models, api

_logger = logging.getLogger(__name__)


class UserMailPoll(models.Model):
    _name = 'user.mail.poll'
    _description = 'IMAP Poller (cron-anknytning)'

    @api.model
    def _poll_cron(self):
        """Cron-entry: polla alla användare med imap_poll_enabled=True."""
        return self.env['user.mail.imap'].action_poll_all()
