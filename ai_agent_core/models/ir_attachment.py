# -*- coding: utf-8 -*-
"""ir.attachment-bridge — koppla uppladdningar till OKF-kön (task 5b.1/5b.2).

Web UI-uppladdning → async-kö (cron processar).
Channel/chat-uppladdning → synkron via message context.
"""

import logging

from odoo import models, api

_logger = logging.getLogger(__name__)


class IrAttachment(models.Model):
    _inherit = 'ir.attachment'

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        try:
            # Koppla till OKF-kön för dokumentliknande bilagor
            for attach in records:
                if not attach.mimetype or attach.mimetype.startswith('text/'):
                    continue
                # Channel/chat-kontext: synkron indexering (task 5b.2)
                channel = self.env.context.get('okf_channel_id')
                coworker = self.env.context.get('okf_coworker_id')
                author = self.env.context.get('okf_author_id')
                if channel:
                    self.env['ai.okf.upload']._process_channel_upload(
                        attach, coworker_id=coworker,
                        channel_id=channel, author_id=author)
                else:
                    # Web UI: async-kö (task 5b.1)
                    self.env['ai.okf.upload']._enqueue_upload(attach)
        except Exception as e:
            _logger.warning('OKF upload bridge failed: %s', e)
        return records
