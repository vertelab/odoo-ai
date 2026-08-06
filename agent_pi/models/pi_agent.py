# Copyright (C) 2026 Vertel AB (<https://vertel.se>).
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

"""
Pi Agent — registered Pi Coding Agents on minions.

Each agent registers itself and reports health via NATS heartbeat.
Agents have skills that determine which tasks they can handle.
"""

import json
import logging
from datetime import datetime, timedelta

from odoo import api, fields, models, _

_logger = logging.getLogger(__name__)

HEARTBEAT_TIMEOUT_MINUTES = 5  # An agent is offline if no heartbeat in 5 minutes


class PiAgent(models.Model):
    _name = "pi.agent"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _description = "Pi Agent"
    _order = "name"

    # --- Identification ---
    name = fields.Char(string="Name", required=True)
    hostname = fields.Char(
        string="Hostname",
        required=True,
        index=True,
        help="Machine hostname or Salt minion ID, e.g. 'fors', 'waland-hermes'",
    )
    salt_minion_id = fields.Char(
        string="Salt Minion ID",
        help="SaltStack minion ID if different from hostname",
    )

    # --- State ---
    state = fields.Selection(
        [
            ("offline", "Offline"),
            ("idle", "Idle"),
            ("busy", "Busy"),
            ("error", "Error"),
        ],
        string="State",
        default="offline",
        compute="_compute_state",
        store=True,
    )
    agent_type = fields.Selection(
        [
            ("controller", "Controller"),
            ("worker", "Worker"),
            ("both", "Both"),
        ],
        string="Agent Type",
        default="worker",
        required=True,
    )
    active = fields.Boolean(string="Active", default=True)

    # --- Capabilities ---
    skill_ids = fields.Many2many(
        "pi.skill", string="Skills",
        help="Skills available on this agent",
    )
    tools = fields.Text(
        string="Tools",
        default='["read","write","edit","bash","grep","find","ls"]',
        help="JSON list of available tools",
    )
    model = fields.Char(
        string="LLM Model",
        default="anthropic/claude-sonnet-4-6",
        help="LLM model used by this agent",
    )
    api_key = fields.Char(string="API Key")

    # --- Health monitoring ---
    last_heartbeat = fields.Datetime(string="Last Heartbeat")
    load_avg = fields.Float(string="Load Average")
    memory_used = fields.Float(string="Memory Used (MB)")
    pi_version = fields.Char(string="Pi Version")
    nats_connected = fields.Boolean(string="NATS Connected", default=False)

    # --- Relations ---
    task_ids = fields.One2many("pi.task", "agent_id", string="Tasks")
    task_count = fields.Integer(
        string="Active Tasks", compute="_compute_task_count", store=True,
    )

    _sql_constraints = [
        ("hostname_uniq", "UNIQUE(hostname)", "Agent hostname must be unique!"),
    ]

    @api.depends("task_ids.state")
    def _compute_task_count(self):
        for rec in self:
            rec.task_count = len(
                rec.task_ids.filtered(lambda t: t.state in ("queued", "running"))
            )

    @api.depends("last_heartbeat", "task_ids.state")
    def _compute_state(self):
        now = fields.Datetime.now()
        timeout = now - timedelta(minutes=HEARTBEAT_TIMEOUT_MINUTES)
        for rec in self:
            if rec.last_heartbeat and rec.last_heartbeat > timeout:
                active_tasks = rec.task_ids.filtered(
                    lambda t: t.state == "running"
                )
                rec.state = "busy" if active_tasks else "idle"
            else:
                rec.state = "offline"

    # ------------------------------------------------------------
    # Heartbeat handling
    # ------------------------------------------------------------
    def _handle_heartbeat(self, payload):
        """Update agent health status from a NATS heartbeat payload."""
        hostname = payload.get("hostname", "")
        agent = self.search([("hostname", "=", hostname)], limit=1)
        if not agent:
            _logger.warning(f"PiAgent: heartbeat from unknown hostname '{hostname}'")
            return False

        agent.write({
            "last_heartbeat": fields.Datetime.now(),
            "load_avg": payload.get("load_avg", 0.0),
            "memory_used": payload.get("memory_used", 0.0),
            "pi_version": payload.get("pi_version", ""),
            "nats_connected": payload.get("nats_connected", False),
        })
        _logger.debug(f"PiAgent: heartbeat from {hostname} OK")
        return True

    def _get_tools_list(self):
        """Return tools as a Python list."""
        self.ensure_one()
        try:
            return json.loads(self.tools or "[]")
        except json.JSONDecodeError:
            return []

    # ------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------
    def action_register(self):
        """Mark agent as active and trigger initial heartbeat request."""
        self.ensure_one()
        self.active = True
        _logger.info(f"PiAgent: registered agent '{self.name}' ({self.hostname})")

    def action_view_tasks(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Tasks for %s") % self.name,
            "res_model": "pi.task",
            "domain": [("agent_id", "=", self.id)],
            "view_mode": "kanban,list,form",
            "target": "current",
        }
