# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.
{
    'name': 'odoo-ai: Pi MCP Developer Server',
    'version': '18.0.1.0.0',
    'summary': 'Standalone MCP streamable-HTTP server for Pi system development against this database',
    'category': 'AI Orchestration',
    'description': """
        ai_pi_mcp — Standalone MCP (Model Context Protocol) developer server.

        Exposes a native MCP Streamable HTTP endpoint (``/mcp``) so an external
        AI coding agent (Pi) can perform system development directly against this
        Odoo database over the standard MCP protocol — no odoo-shell, no
        checkmodule, no SSH round-trips.

        Purpose
        =======
        * Edit form / kanban / list views directly in the database
          (``ir.ui.view`` arch, ``ir.actions.act_window``, ``ir.model``).
        * Install / upgrade / uninstall modules via the public
          ``button_immediate_*`` methods on ``ir.module.module``.
        * Introspect arbitrary models (``describe_model``, list fields, access
          rights) and read/search/count records.
        * Fix or correct data through a guarded write path.
        * View edits are automatically backed up before every change and can be
          rolled back.

        Design
        ======
        * Fully standalone — depends only on ``base`` / ``web``. No third-party
          MCP framework is pulled in; the MCP streamable-HTTP transport is
          implemented natively on an ``http.Controller``.
        * Speaks the *stateful* MCP protocol revision ``2025-03-26`` (session
          based), which is what the official MCP SDK (v1.29.x) negotiates and
          the simplest correct revision to serve.
        * Bearer authentication against the Odoo user database via an
          ``ir.config_parameter``-held API token (``ai_pi_mcp.api_key``).
          Callers act as the configured developer user (``group_system``) so the
          development tools can touch ``ir.ui.view`` / ``ir.module.module``.
        * **Development-only.** Guarded by an explicit opt-in
          (``ai_pi_mcp.enabled``) and intended for test/development databases.

        Security guards
        ===============
        * View arch edits are snapshotted to ``ir.attachment`` before every
          write and can be restored with ``view_restore``.
        * ``call_method`` is restricted to public methods (no leading ``_``).
        * Origin / DNS-rebinding protection on the endpoint.
    """,
    'author': 'Vertel AB',
    'website': 'https://vertel.se/apps/odoo-ai/ai_pi_mcp',
    'license': 'AGPL-3',
    'depends': [
        'base',
        'web',
    ],
    'data': [
        'security/ir.model.access.csv',
    ],
    'installable': True,
    'application': False,
    'auto_install': False,
}
