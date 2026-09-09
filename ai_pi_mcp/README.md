# ai_pi_mcp

Standalone MCP (Model Context Protocol) **streamable-HTTP** developer server for
Odoo 18. Lets an external AI coding agent (Pi) perform system development
directly against this database over the standard MCP protocol — editing views,
managing modules, introspecting models and fixing data — without odoo-shell or
checkmodule round-trips.

## What it does

* **Views** — list / read the merged (effective) arch for a model+type, replace a
  view's arch (auto-backed-up to `ir.attachment` before every change and
  restorable), drive `ir.actions.act_window` (domain / view_mode / order).
* **Modules** — list, install, upgrade and uninstall `ir.module.module` through
  the public `button_immediate_*` methods.
* **Introspection** — `list_models`, `describe_model`, `view_list`, `action_list`,
  `module_list`, `whoami`, `get_access_rights`.
* **Data** — `search_read`, `read_records`, `count`, `create_record`,
  `update_record`, `delete_record`, and `call_method` (public methods only).

## Transport & protocol

* Single endpoint **`POST /mcp`** speaking the *stateful* MCP protocol revision
  **`2025-03-26`** (session based), which the official MCP SDK (v1.29.x)
  negotiates and accepts.
* `GET /mcp` returns `405` (no SSE stream offered — the SDK treats this as
  expected). `DELETE /mcp` terminates the session.
* Replies to requests are returned as direct `application/json` (the SDK accepts
  plain JSON for non-streaming servers).
* **No third-party MCP framework and no extra Python dependencies** — the
  transport is implemented natively on an `http.Controller`.

## Authentication

Bearer token against the `ir.config_parameter` key **`ai_pi_mcp.api_key`**.
Authenticated calls act as the user in **`ai_pi_mcp.developer_user_id`** (which
should hold `group_system` on a development database so the tools can touch
`ir.ui.view` / `ir.module.module`).

## Configuration (ir.config_parameter)

| Key | Meaning |
|-----|---------|
| `ai_pi_mcp.enabled` | `True` to serve `/mcp`. Dev-only; default `False`. |
| `ai_pi_mcp.api_key` | The bearer token clients must send. |
| `ai_pi_mcp.developer_user_id` | Odoo user id whose rights tools run with. |
| `ai_pi_mcp.read_only` | `True` to block all write-capable tools. |

## Security guards

* **Development-only** — disabled unless `ai_pi_mcp.enabled=True`.
* View arch edits are snapshotted to `ir.attachment` before each change and can
  be restored with the `view_restore` tool.
* `call_method` is restricted to public methods (no leading underscore).
* Read-only mode can be switched on per deployment.

## Usage from Pi

Register in `~/.pi/agent/mcp.json`:

```json
{
  "mcpServers": {
    "odoo-sfa": {
      "transport": "streamable-http",
      "url": "http://localhost:8069/mcp",
      "headers": { "Authorization": "Bearer <ai_pi_mcp.api_key>" },
      "lifecycle": "lazy"
    }
  }
}
```

Tools then appear in Pi as `mcp__odoo-sfa__<tool>`.
