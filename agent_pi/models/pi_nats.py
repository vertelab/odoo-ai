# Copyright (C) 2026 Vertel AB (<https://vertel.se>).
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

"""
Pi NATS client — singleton NATS connection for agent_pi.

Manages a single NATS connection per Odoo process.
Uses nats-py for publish/subscribe to NATS subjects.
"""

import json
import logging

from odoo import models, fields, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class PiNatsClient(models.AbstractModel):
    _name = "pi.nats"
    _description = "Pi NATS Client"

    # ------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------
    @api.model
    def _get_nats_url(self):
        """Get NATS server URL from system parameters."""
        return (
            self.env["ir.config_parameter"]
            .sudo()
            .get_param("pi.nats_url", "nats://localhost:4222")
        )

    @api.model
    def _get_jetstream_enabled(self):
        """Check if JetStream is enabled."""
        return (
            self.env["ir.config_parameter"]
            .sudo()
            .get_param("pi.nats_jetstream_enabled", "True")
            .lower() == "true"
        )

    # ------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------
    @api.model
    def _get_client(self):
        """Get or create a NATS connection.

        Returns a connected NATS client. Uses the import-on-demand pattern
        to avoid crashing when nats-py is not installed.
        """
        try:
            import nats
        except ImportError:
            raise UserError(
                _(
                    "nats-py is not installed. Install it with:\n"
                    "pip install nats-py"
                )
            )

        nats_url = self._get_nats_url()
        try:
            nc = nats.connect(nats_url)
            _logger.info(f"PiNats: connected to {nats_url}")
            return nc
        except Exception as e:
            _logger.error(f"PiNats: failed to connect to {nats_url}: {e}")
            raise UserError(
                _("Failed to connect to NATS at %s: %s") % (nats_url, str(e))
            )

    # ------------------------------------------------------------
    # Publish
    # ------------------------------------------------------------
    @api.model
    def publish(self, subject, payload):
        """Publish a message to a NATS subject.

        Args:
            subject: NATS subject string, e.g. 'pi.task.new'
            payload: dict to be JSON-encoded and published

        Returns:
            str: NATS message ID or None
        """
        nc = self._get_client()
        try:
            data = json.dumps(payload).encode("utf-8")
            ack = nc.publish(subject, data)
            _logger.debug(f"PiNats: published to '{subject}' ({len(data)} bytes)")
            nc.close()
            return ack
        except Exception as e:
            _logger.error(f"PiNats: publish failed to '{subject}': {e}")
            nc.close()
            raise

    # ------------------------------------------------------------
    # Subscribe (async — for long-running processes)
    # ------------------------------------------------------------
    @api.model
    async def subscribe(self, subject, callback):
        """Subscribe to a NATS subject with an async callback.

        Args:
            subject: NATS subject to subscribe to
            callback: async callable(msg) that processes incoming messages

        Note:
            This method is async and intended for use in long-running
            processes (Pi agents), not in standard Odoo request/response cycles.
        """
        try:
            import nats
        except ImportError:
            raise UserError(_("nats-py is not installed."))

        nats_url = self._get_nats_url()
        nc = await nats.connect(nats_url)

        async def handler(msg):
            try:
                data = json.loads(msg.data.decode("utf-8"))
                await callback(data)
            except Exception as e:
                _logger.error(f"PiNats: callback error for '{subject}': {e}")

        await nc.subscribe(subject, cb=handler)
        _logger.info(f"PiNats: subscribed to '{subject}'")
        return nc

    # ------------------------------------------------------------
    # JetStream helpers
    # ------------------------------------------------------------
    @api.model
    async def jetstream_publish(self, subject, payload):
        """Publish to a JetStream subject with persistence.

        Requires JetStream to be enabled on the NATS server.
        """
        try:
            import nats
            from nats.js import JetStreamContext
        except ImportError:
            raise UserError(_("nats-py is not installed."))

        nats_url = self._get_nats_url()
        nc = await nats.connect(nats_url)
        js = nc.jetstream()

        data = json.dumps(payload).encode("utf-8")
        ack = await js.publish(subject, data)
        _logger.debug(f"PiNats: JetStream publish to '{subject}' (ack={ack})")
        await nc.close()
        return ack
