# -*- coding: utf-8 -*-
"""
ai_agent_zabbix — Zabbix 7.0 JSON-RPC integration.

Sends Zabbix events when quest caps are exceeded.
Reuses the pattern from paperclip/employees/files/zabbix_client.py.
"""

import json
import logging
import ssl
import urllib.request
import urllib.error
from odoo import models, fields, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class AIZabbixConfig(models.Model):
    """Zabbix connection configuration."""
    _name = 'ai.zabbix.config'
    _description = 'Zabbix Configuration'

    name = fields.Char('Name', default='Zabbix')
    active = fields.Boolean(default=True)
    url = fields.Char('Zabbix URL', required=True,
                      default='https://zabbix.vertel.se/api_jsonrpc.php',
                      help='Full URL to Zabbix JSON-RPC endpoint')
    api_token = fields.Char('API Token', required=True,
                            help='Zabbix API token for authentication')
    timeout = fields.Integer('Timeout (s)', default=30)
    verify_ssl = fields.Boolean('Verify SSL', default=False)

    # Status
    last_test = fields.Datetime('Last Test')
    connection_ok = fields.Boolean('Connection OK')

    def _zabbix_call(self, method, params=None):
        """Call Zabbix JSON-RPC API. Returns result dict."""
        self.ensure_one()
        payload = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params or {},
            "id": 1,
        }
        # apiinfo.version must NOT receive auth; all other methods require it
        if method != 'apiinfo.version':
            payload["auth"] = self.api_token
        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            self.url,
            data=data,
            headers={"Content-Type": "application/json-rpc"},
        )
        try:
            ctx = ssl.create_default_context()
            if not self.verify_ssl:
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
            with urllib.request.urlopen(req, timeout=self.timeout, context=ctx) as resp:
                result = json.loads(resp.read().decode())
                if "error" in result:
                    raise UserError(_(
                        'Zabbix API error: %s'
                    ) % result['error'].get('data', result['error']))
                return result.get("result")
        except urllib.error.HTTPError as e:
            raise UserError(_('Zabbix HTTP %d: %s') % (e.code, e.read().decode()[:200]))
        except urllib.error.URLError as e:
            raise UserError(_('Zabbix connection failed: %s') % e.reason)

    def send_event(self, name, value=1, tags=None):
        """Send an event to Zabbix.

        Args:
            name: Event name (appears in Zabbix Problems)
            value: Event value (1=problem, 0=ok)
            tags: Optional dict of tags for the event
        """
        self.ensure_one()
        try:
            params = {
                "name": name,
                "value": value,
                "tags": tags or {},
            }
            self._zabbix_call("event.create", params)
            _logger.info("Zabbix event sent: %s (value=%s)", name, value)
            return True
        except Exception as e:
            _logger.error("Failed to send Zabbix event: %s", e)
            return False

    def action_test_connection(self):
        """Test Zabbix API connection."""
        self.ensure_one()
        try:
            result = self._zabbix_call("apiinfo.version")
            self.connection_ok = True
            self.last_test = fields.Datetime.now()
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Zabbix OK'),
                    'message': _('Connected to Zabbix %s') % result,
                    'type': 'success',
                }
            }
        except Exception as e:
            self.connection_ok = False
            raise UserError(_('Zabbix test failed: %s') % str(e))

    # ── Read-only query-metoder (Zabbix Analyst-tools, drift-ai-coworkers 3.2) ──

    def _zabbix_problems(self, limit=20):
        """Aktiva problem (triggers i PROBLEM-status)."""
        self.ensure_one()
        result = self._zabbix_call("problem.get", {
            "output": ["eventid", "objectid", "severity", "clock", "name"],
            "recent": True,
            "sortfield": ["eventid"],
            "sortorder": "DESC",
            "limit": limit,
        }) or []
        return [{
            "event_id": p.get("eventid"),
            "trigger_id": p.get("objectid"),
            "severity": p.get("severity"),
            "clock": p.get("clock"),
            "name": p.get("name"),
        } for p in result]

    def _zabbix_hosts(self, limit=50):
        """Värdar med status."""
        self.ensure_one()
        result = self._zabbix_call("host.get", {
            "output": ["hostid", "host", "name", "status", "available"],
            "limit": limit,
        }) or []
        return [{
            "host_id": h.get("hostid"),
            "host": h.get("host"),
            "name": h.get("name"),
            "status": h.get("status"),
            "available": h.get("available"),
        } for h in result]

    def _zabbix_alerts(self, limit=20):
        """Senaste händelser (events)."""
        self.ensure_one()
        result = self._zabbix_call("event.get", {
            "output": ["eventid", "objectid", "source", "severity", "clock", "value", "name"],
            "sortfield": ["clock"],
            "sortorder": "DESC",
            "limit": limit,
        }) or []
        return [{
            "event_id": e.get("eventid"),
            "trigger_id": e.get("objectid"),
            "source": e.get("source"),
            "severity": e.get("severity"),
            "clock": e.get("clock"),
            "value": e.get("value"),
            "name": e.get("name"),
        } for e in result]

    def notify_cap_exceeded(self, quest):
        """Send Zabbix event when quest cap is exceeded.

        Called from ai.quest._notify_cap() via automated action or direct hook.
        """
        self.ensure_one()
        if not self.active:
            return False
        return self.send_event(
            name=f"AI cap exceeded: {quest.name}",
            value=1,
            tags={
                "quest_id": str(quest.id),
                "quest_name": quest.name,
                "customer": quest.company_id.name if quest.company_id else "unknown",
                "cap": str(quest.monthly_cap_mtokens),
                "used": str(quest.started_mtokens),
            }
        )
