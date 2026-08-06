# Copyright (C) 2026 Vertel AB (<https://vertel.se>).
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

"""
Pi Callback Controller — receives results from Pi agents via NATS.

Endpoint: POST /pi/callback/<task_id>
Called by the controller Pi agent when a worker completes a task.
"""

import json
import logging

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)


class PiCallbackController(http.Controller):

    @http.route(
        "/pi/callback/<int:task_id>",
        type="json",
        auth="user",
        methods=["POST"],
        csrf=False,
    )
    def pi_callback(self, task_id, **payload):
        """Receive a task result from the Pi agent controller.

        Expected payload:
        {
            "task_id": int,
            "state": "done" | "failed" | "timeout",
            "result": "text result from agent",
            "artifacts": [
                {
                    "name": "screenshot_form.png",
                    "type": "image",
                    "content": "base64...",
                    "mimetype": "image/png",
                    "url": "",
                    "filename": "screenshot_form.png"
                }
            ]
        }
        """
        _logger.info(f"PiCallback: received result for task #{task_id}")

        task = request.env["pi.task"].browse(task_id)
        if not task.exists():
            _logger.error(f"PiCallback: task #{task_id} not found")
            return {"status": "error", "message": f"Task #{task_id} not found"}

        try:
            task._handle_result(payload)
            return {"status": "ok", "task_id": task_id}
        except Exception as e:
            _logger.exception(f"PiCallback: error processing task #{task_id}")
            return {"status": "error", "message": str(e)}

    @http.route(
        "/pi/health",
        type="json",
        auth="user",
        methods=["POST"],
        csrf=False,
    )
    def pi_health(self, **payload):
        """Receive a health heartbeat from a Pi agent.

        Expected payload:
        {
            "hostname": "fors",
            "load_avg": 0.5,
            "memory_used": 1024.0,
            "pi_version": "1.2.3",
            "nats_connected": true
        }
        """
        hostname = payload.get("hostname", "")
        _logger.debug(f"PiHealth: heartbeat from '{hostname}'")

        try:
            request.env["pi.agent"]._handle_heartbeat(payload)
            return {"status": "ok", "hostname": hostname}
        except Exception as e:
            _logger.error(f"PiHealth: error processing heartbeat: {e}")
            return {"status": "error", "message": str(e)}
