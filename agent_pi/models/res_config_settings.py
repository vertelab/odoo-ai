# Copyright (C) 2026 Vertel AB (<https://vertel.se>).
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    pi_nats_url = fields.Char(
        string="NATS URL",
        default="nats://localhost:4222",
        config_parameter="pi.nats_url",
        help="NATS server connection URL, e.g. 'nats://localhost:4222'",
    )
    pi_nats_jetstream_enabled = fields.Boolean(
        string="JetStream Enabled",
        default=True,
        config_parameter="pi.nats_jetstream_enabled",
        help="Enable JetStream for message persistence, retry, and dead-letter queues",
    )
