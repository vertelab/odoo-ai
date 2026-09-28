# -*- coding: utf-8 -*-
"""ir.attachment-bridge — koppla uppladdningar till OKF-kön (task 5b.1/5b.2).

Web UI-uppladdning → async-kö (cron processar).
Channel/chat-uppladdning → synkron via message context.

VARFÖR ETT URVAL (fix 2026-09-22)
---------------------------------
Bron skickade tidigare ALLT som inte var `text/*` till kunskapskön. Följden i
drift: 356 `image/svg+xml` (ikoner, logotyper, UI-assets), 58
`application/javascript` (kod-assets), 10 `application/zip` och en rad
Office-format hamnade i kön. De kunde aldrig bli kunskap — men de skapade
1391 poster i `state='error'` och belastade cron 669 varannan minut.

En kunskapskö ska ta emot DOKUMENT. En ikon är inte ett dokument.
MIME-grinden (`_is_knowledge_candidate`) är därför den enda grinden:
rätt sorts fil, oavsett vem som skapade den.

OBS: en tidigare variant försökte även kräva en `okf_author_id`/`okf_explicit`
i context. Det var fel — ingen kod sätter dessa nycklar, så grinden hade
stängt av ALL indexering. MIME-grinden räcker: den filtrerar bort exakt de
klasser som aldrig kunde bli kunskap (SVG-ikoner, JS-assets, zip).
"""

import logging

from odoo import models, api

_logger = logging.getLogger(__name__)

# MIME-typer som ÄR kunskapskandidater — dokument en människa vill kunna
# söka i senare. Allt utanför denna lista ignoreras tyst.
KNOWLEDGE_MIMETYPES = frozenset([
    'application/pdf',
    'application/msword',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'application/vnd.oasis.opendocument.text',
    'application/vnd.oasis.opendocument.presentation',
    'application/vnd.oasis.opendocument.spreadsheet',
    'application/vnd.ms-excel',
    'application/vnd.ms-powerpoint',
    'application/vnd.openxmlformats-officedocument.presentationml.presentation',
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    'message/rfc822',
    'message/global',
])

# MIME-prefix som är kunskapskandidater (foton, skärmdumpar, scanned docs).
KNOWLEDGE_MIME_PREFIXES = ('image/',)

# Undantag: dessa är bilder men INTE dokument — de är UI-assets.
# SVG är dessutom XML-text och ger ingen meningsfull OCR.
NON_KNOWLEDGE_MIMETYPES = frozenset([
    'image/svg+xml',
    'image/x-icon',
    'image/vnd.microsoft.icon',
])


class IrAttachment(models.Model):
    _inherit = 'ir.attachment'

    @api.model
    def _is_knowledge_candidate(self, attach):
        """Är bilagan ett dokument som hör i kunskapskön?

        Returnerar True endast för dokument och foton — inte för ikoner,
        kod-assets eller arkiv. Se modulens docstring för bakgrunden.
        """
        mimetype = (attach.mimetype or '').lower()
        if not mimetype:
            return False
        if mimetype in NON_KNOWLEDGE_MIMETYPES:
            return False
        if mimetype in KNOWLEDGE_MIMETYPES:
            return True
        return mimetype.startswith(KNOWLEDGE_MIME_PREFIXES)

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        try:
            for attach in records:
                # Grind: rätt sorts fil (dokument/foto, inte asset/binär).
                if not self._is_knowledge_candidate(attach):
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
                    self.env['ai.okf.upload']._enqueue_upload(
                        attach, owner_user_id=author)
        except Exception as e:
            _logger.warning('OKF upload bridge failed: %s', e)
        return records
