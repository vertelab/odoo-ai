# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.
"""MCP session persistence for the stateful protocol revision.

A session is created on ``initialize`` and terminated on ``DELETE``. It records
the Odoo user the caller is acting as plus the negotiated protocol version, so a
later request that omits the version header is still served under the right
revision.
"""

import secrets

from odoo import api, fields, models


class McpSession(models.Model):
    _name = 'mcp.session'
    _description = 'MCP Session'
    _rec_name = 'session_id'

    session_id = fields.Char(
        string='Session ID',
        required=True,
        readonly=True,
        index=True,
    )
    user_id = fields.Many2one(
        'res.users',
        string='Odoo User',
        required=True,
        readonly=True,
        index=True,
    )
    protocol_version = fields.Char(
        string='Protocol Version',
        required=True,
        readonly=True,
    )
    initialized = fields.Boolean(
        string='Initialized',
        default=False,
    )
    active = fields.Boolean(
        string='Active',
        default=True,
    )
    create_date = fields.Datetime(
        string='Created',
        readonly=True,
    )
    last_seen = fields.Datetime(
        string='Last Seen',
        readonly=True,
    )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get('session_id'):
                vals['session_id'] = 'mcp_' + secrets.token_urlsafe(24)
        records = super().create(vals_list)
        return records

    def _touch(self):
        """Refresh ``last_seen`` and prune stale sessions in the same write."""
        self.write({'last_seen': fields.Datetime.now()})
        # Opportunistic garbage collection of sessions idle > 6 hours.
        self.env['mcp.session'].search([
            ('active', '=', True),
            ('last_seen', '<',
             fields.Datetime.subtract(fields.Datetime.now(), hours=6)),
        ]).write({'active': False})
        return self
