# Copyright (C) 2026 Vertel AB (<https://vertel.se>).
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

"""
Pi Task — agent task with priority, retry, skills, and NATS integration.

Tasks are published to NATS subject `pi.task.new` with their skills.
A controller Pi agent picks them up and assigns to a worker.
Results come back via `/pi/callback/<task_id>`.
"""

import json
import logging
import uuid
from datetime import datetime

from odoo import api, fields, models, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

NATS_SUBJECT_NEW = "pi.task.new"
NATS_SUBJECT_ASSIGN = "pi.task.assign"
NATS_SUBJECT_RESULT = "pi.result"
DEFAULT_MAX_RETRIES = 3


class PiTask(models.Model):
    _name = "pi.task"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _description = "Pi Agent Task"
    _order = "priority desc, create_date desc"

    # --- Core ---
    name = fields.Char(string="Name", required=True)
    prompt = fields.Text(
        string="Prompt",
        required=True,
        help="Instructions for the Pi agent. Can reference skills.",
    )
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("queued", "Queued"),
            ("running", "Running"),
            ("done", "Done"),
            ("failed", "Failed"),
        ],
        string="State",
        default="draft",
        tracking=True,
    )
    priority = fields.Selection(
        [
            ("0", "Low"),
            ("1", "Medium"),
            ("2", "High"),
            ("3", "Critical"),
        ],
        string="Priority",
        default="1",
        required=True,
    )

    # --- Retry ---
    max_retries = fields.Integer(string="Max Retries", default=DEFAULT_MAX_RETRIES)
    retry_count = fields.Integer(string="Retry Count", default=0)

    # --- Assignment ---
    agent_id = fields.Many2one(
        "pi.agent", string="Assigned Agent",
        help="Agent assigned by the controller",
    )
    skill_ids = fields.Many2many(
        "pi.skill", string="Skills",
        help="Skills sent alongside the task to the Pi agent",
    )
    requested_agent_type = fields.Selection(
        [("controller", "Controller"), ("worker", "Worker"), ("any", "Any")],
        string="Requested Agent Type",
        default="worker",
    )

    # --- Result ---
    result = fields.Text(string="Result")
    result_state = fields.Selection(
        [("success", "Success"), ("error", "Error"), ("timeout", "Timeout")],
        string="Result State",
    )
    artifact_ids = fields.One2many("pi.artifact", "task_id", string="Artifacts")
    artifact_count = fields.Integer(
        string="Artifacts", compute="_compute_artifact_count", store=True,
    )

    # --- NATS ---
    nats_message_id = fields.Char(
        string="NATS Message ID",
        help="Unique ID for tracing the message through NATS",
    )
    nats_subject = fields.Char(
        string="NATS Subject",
        help="Subject the task was published to",
    )

    # --- Timing ---
    start_time = fields.Datetime(string="Start Time")
    end_time = fields.Datetime(string="End Time")
    duration = fields.Float(
        string="Duration (s)", compute="_compute_duration", store=True,
    )

    # --- Log ---
    log = fields.Text(string="Log")

    @api.depends("artifact_ids")
    def _compute_artifact_count(self):
        for rec in self:
            rec.artifact_count = len(rec.artifact_ids)

    @api.depends("start_time", "end_time")
    def _compute_duration(self):
        for rec in self:
            if rec.start_time and rec.end_time:
                delta = rec.end_time - rec.start_time
                rec.duration = delta.total_seconds()
            else:
                rec.duration = 0.0

    # ------------------------------------------------------------
    # NATS publish
    # ------------------------------------------------------------
    def _build_nats_payload(self):
        """Build the NATS payload for this task."""
        self.ensure_one()
        return {
            "task_id": self.id,
            "task_name": self.name,
            "prompt": self.prompt,
            "priority": int(self.priority),
            "max_retries": self.max_retries,
            "skills": [
                {"name": s.name, "technical_name": s.technical_name, "instruction": s.instruction}
                for s in self.skill_ids
            ],
            "requested_agent_type": self.requested_agent_type,
            "nats_message_id": self.nats_message_id,
            "callback_url": f"/pi/callback/{self.id}",
        }

    def action_run(self):
        """Publish task to NATS subject pi.task.new."""
        self.ensure_one()
        if self.state not in ("draft", "failed"):
            raise UserError(_("Task must be in Draft or Failed state to run."))

        # Generate message ID
        self.nats_message_id = str(uuid.uuid4())

        # Build payload
        payload = self._build_nats_payload()

        # Publish to NATS
        try:
            nats_client = self.env["pi.nats"]._get_client()
            nats_client.publish(
                NATS_SUBJECT_NEW,
                json.dumps(payload).encode("utf-8"),
            )
            self.write({
                "state": "queued",
                "nats_subject": NATS_SUBJECT_NEW,
                "log": f"{fields.Datetime.now()}: Published to NATS subject '{NATS_SUBJECT_NEW}' (msg_id={self.nats_message_id})\n",
            })
            _logger.info(f"PiTask #{self.id}: published to NATS '{NATS_SUBJECT_NEW}'")
        except Exception as e:
            self.write({
                "state": "failed",
                "result_state": "error",
                "result": str(e),
                "log": f"{fields.Datetime.now()}: Failed to publish to NATS: {e}\n",
            })
            raise UserError(_("Failed to publish to NATS: %s") % str(e))

        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Task Queued"),
                "message": _("Task '%s' published to NATS.") % self.name,
                "type": "success",
            },
        }

    def action_retry(self):
        """Retry a failed task."""
        self.ensure_one()
        if self.state != "failed":
            raise UserError(_("Only failed tasks can be retried."))

        if self.retry_count >= self.max_retries:
            raise UserError(
                _("Max retries (%d) exceeded for this task.") % self.max_retries
            )

        self.retry_count += 1
        self.write({
            "state": "draft",
            "result": False,
            "result_state": False,
            "log": self.log + f"{fields.Datetime.now()}: Retry #{self.retry_count}\n",
        })
        return self.action_run()

    # ------------------------------------------------------------
    # Result handling (called from callback controller)
    # ------------------------------------------------------------
    def _handle_result(self, payload):
        """Process a result payload from NATS callback."""
        task_id = payload.get("task_id")
        if not task_id:
            _logger.error("PiTask: result payload missing task_id")
            return False

        task = self.browse(task_id)
        if not task.exists():
            _logger.error(f"PiTask: task #{task_id} not found")
            return False

        state = payload.get("state", "done")
        result = payload.get("result", "")
        artifacts = payload.get("artifacts", [])

        # Store artifacts
        for art in artifacts:
            self.env["pi.artifact"].create({
                "task_id": task.id,
                "name": art.get("name", "unnamed"),
                "artifact_type": art.get("type", "text"),
                "content": art.get("content", ""),
                "mimetype": art.get("mimetype", "text/plain"),
                "url": art.get("url", ""),
                "filename": art.get("filename", ""),
            })

        values = {
            "state": state,
            "result_state": "success" if state == "done" else "error",
            "result": result,
            "end_time": fields.Datetime.now(),
            "log": task.log + f"{fields.Datetime.now()}: Result received (state={state})\n",
        }
        task.write(values)
        _logger.info(f"PiTask #{task.id}: result processed (state={state})")
        return True

    # ------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------
    def action_view_artifacts(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Artifacts for %s") % self.name,
            "res_model": "pi.artifact",
            "domain": [("task_id", "=", self.id)],
            "view_mode": "kanban,list,form",
            "target": "current",
        }
