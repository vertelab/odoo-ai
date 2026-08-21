# -*- coding: utf-8 -*-
"""Poller-piggyback: ärv user.mail.imap och konsumera normaliserade mail."""

import logging

from odoo import models

_logger = logging.getLogger(__name__)


class UserMailImapAI(models.AbstractModel):
    _inherit = 'user.mail.imap'

    def _on_new_messages(self, messages):
        """Konsumera normaliserade mail → triage-pipeline (ingest → klass → dispatch).

        Körs i användarens kontext (with_user) — env.user är ägaren.
        """
        Mail = self.env['user_mail_ai.mail']
        ingested = 0
        for msg in messages:
            try:
                triage = Mail._ingest_message(msg, user=self.env.user)
                if triage:
                    ingested += 1
            except Exception as e:
                _logger.error(
                    'Mail ingest failed for %s (%s): %s',
                    msg.get('subject', '?'), self.env.user.login, e)
        if ingested:
            try:
                Mail._process_new_for_user(self.env.user)
            except Exception as e:
                _logger.error('Mail pipeline failed for %s: %s',
                              self.env.user.login, e)
        return ingested
