# -*- coding: utf-8 -*-
"""
pi.mesh.message — varje meddelande mellan agenter.

Specen säger: "Varje meddelande mellan agenter SHALL bevaras och vara
sökbart." Det är hela poängen med modulen. När någon i efterhand vill
veta vem som rörde en fil, eller vem som frågade vad, ska svaret finnas
här — inte i en flyktig NATS-ström som redan passerat.
"""

import logging

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

# Meddelanden gallras efter denna ålder om de inte hör till en pågående
# uppgift. Specen är tydlig: städning får inte röra pågående arbete.
DEFAULT_RETENTION_DAYS = 30


class PiMeshMessage(models.Model):
    _name = 'pi.mesh.message'
    _description = 'Meddelande mellan Pi-agenter'
    _order = 'sent_at desc, id desc'

    agent_id = fields.Char('Från', required=True, index=True)
    recipient = fields.Char(
        'Till', index=True,
        help='Tomt = broadcast till alla agenter.',
    )
    kind = fields.Selection(
        [('message', 'Meddelande'),
         ('query', 'Fråga'),
         ('response', 'Svar'),
         ('event', 'Händelse'),
         ('claim', 'Anspråk'),
         ('lock', 'Lås'),
         ('heartbeat', 'Heartbeat')],
        'Typ', required=True, default='message', index=True,
    )
    subject = fields.Char('Ämne')
    body = fields.Text('Innehåll')
    response = fields.Text('Svar')
    sent_at = fields.Datetime(
        'Skickat', default=fields.Datetime.now, required=True, index=True,
    )
    answered_at = fields.Datetime('Besvarat')
    correlation_id = fields.Char(
        'Korrelations-ID', index=True,
        help='Knyter ihop en fråga med sitt svar.',
    )
    task_id = fields.Many2one(
        'pi.mesh.task', 'Uppgift', ondelete='set null', index=True,
    )
    session_ref = fields.Char('Session', help='Pi-sessionens id, om känd.')

    answered = fields.Boolean('Besvarat', compute='_compute_answered', store=True)
    age_days = fields.Float('Ålder (dagar)', compute='_compute_age_days')

    @api.depends('answered_at')
    def _compute_answered(self):
        for msg in self:
            msg.answered = bool(msg.answered_at)

    @api.depends('sent_at')
    def _compute_age_days(self):
        now = fields.Datetime.now()
        for msg in self:
            if msg.sent_at:
                delta = now - msg.sent_at
                msg.age_days = delta.total_seconds() / 86400.0
            else:
                msg.age_days = 0.0

    # ── Städning ───────────────────────────────────────────────────

    @api.model
    def _cron_purge_old_messages(self):
        """Gallra gamla meddelanden — men aldrig pågående uppgifter.

        Specen: "WHEN meddelanden är äldre än konfigurerad gräns THEN
        gallras de utan att röra poster som hör till pågående uppgifter."

        Därför undantas varje meddelande med en `task_id` vars uppgift
        inte är klar. Ett avslutat arbete får däremot gallras med resten.
        """
        days = self._retention_days()
        cutoff = fields.Datetime.subtract(fields.Datetime.now(), days=days)

        # Meddelanden som hör till en pågående uppgift skyddas.
        active_tasks = self.env['pi.mesh.task'].search([
            ('status', 'in', ('pending', 'assigned', 'running')),
        ])
        protected = active_tasks.ids

        domain = [('sent_at', '<', cutoff)]
        if protected:
            domain.append(('task_id', 'not in', protected))

        doomed = self.search(domain)
        count = len(doomed)
        if count:
            doomed.unlink()
            _logger.info(
                'pi_mesh: gallrade %d meddelanden äldre än %d dagar '
                '(%d pågående uppgifter skyddade)', count, days, len(protected),
            )
        return count

    @api.model
    def _retention_days(self):
        raw = self.env['ir.config_parameter'].sudo().get_param(
            'pi_mesh.message_retention_days', DEFAULT_RETENTION_DAYS,
        )
        try:
            return int(raw)
        except (TypeError, ValueError):
            return DEFAULT_RETENTION_DAYS
