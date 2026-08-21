# -*- coding: utf-8 -*-
"""Catchall/Discuss-ingestion: mail som Odoo fångat (mailgateway) → triage.

Hook på mail.message.create. Filtrering:
- message_type='email' + icke-intern subtype (kommentarer/notiser skippas)
- avsändare får inte vara en intern användare (våra egna svar skippas)
- hjälpredans egna posts sätts med context-flaggan user_mail_ai_no_ingest
- Message-ID-dedup skyddar mot dubbel med IMAP-pollen (Skiva 1)
"""

import logging

from odoo import api, models

_logger = logging.getLogger(__name__)


class MailMessageAI(models.Model):
    _inherit = 'mail.message'

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        if self.env.context.get('user_mail_ai_no_ingest'):
            return records
        for rec in records:
            try:
                self._maybe_ingest(rec)
            except Exception as e:
                _logger.warning('Mail ingest hook failed (%s): %s',
                                rec.id, e)
        return records

    def _maybe_ingest(self, rec):
        """Ingestera inkommande email-mail → samma triage-pipeline."""
        if rec.message_type != 'email':
            return
        if rec.subtype_id and rec.subtype_id.internal:
            return
        if not rec.model or not rec.res_id:
            return
        if rec.author_id:
            user = self.env['res.users'].search(
                [('partner_id', '=', rec.author_id.id)], limit=1)
            if user:
                # Avsändaren är en intern användare (vårt eget svar) → skippa
                return
        try:
            self.env['user_mail_ai.mail']._ingest_mail_message(rec)
        except Exception as e:
            _logger.warning('Ingest mail.message %s failed: %s', rec.id, e)
