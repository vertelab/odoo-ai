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

## Bundled skills (Pi reads from filesystem)

`skills/` holds MCP-native methodology documents that Pi loads from the module
filesystem (not over MCP, so `ai.coworkers` without MCP access are unaffected):

* `skills/view-design.md` — five-phase view-design dialog for all view types,
  discovery/iteration via the MCP tools against live `ir.ui.view`; approved
  views are persisted into module source + verified with `checkmodule`.
  (ai_pi_mcp-native equivalent of `/skill:odoo-view`.)
* `skills/kanban-design.md` — same MCP workflow, kanban-specific card design.
  (ai_pi_mcp-native equivalent of `/skill:odoo-kanban`.)
* `skills/improvements.md` — metareflection log (created on first use).

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

By default the caller authenticates with **their own per-user Odoo API key**
(the same mechanism `/ai/v1` uses): the Bearer token is validated against
`res.users.apikeys` and the request runs as **that user**. No shared key or
hardcoded developer user is needed — the acting (developer) user defaults to
the API-key owner.

If **`ai_pi_mcp.developer_user_id`** is configured it acts as an explicit
override (a fixed service account); the token is still validated either as the
caller's own per-user key or against the legacy shared `ai_pi_mcp.api_key`.

The acting user should hold `group_system` on a development database so the
tools can touch `ir.ui.view` / `ir.module.module`.

## Configuration (ir.config_parameter)

| Key | Meaning |
|-----|---------|
| `ai_pi_mcp.enabled` | `True` to serve `/mcp`. Dev-only; default `False`. |
| `ai_pi_mcp.api_key` | Legacy shared bearer token (optional). Only needed when a fixed `developer_user_id` override is used. |
| `ai_pi_mcp.developer_user_id` | Optional explicit override: Odoo user id whose rights tools run with. When unset, the caller's own API-key account is used. |
| `ai_pi_mcp.read_only` | `True` to block all write-capable tools. |

## Security guards

* **Development-only** — disabled unless `ai_pi_mcp.enabled=True`.
* View arch edits are snapshotted to `ir.attachment` before each change and can
  be restored with the `view_restore` tool.
* `call_method` is restricted to public methods (no leading underscore).
* Read-only mode can be switched on per deployment.

## Usage from Pi

Register in `~/.pi/agent/mcp.json` (use your own Odoo API key — the same one
as in `~/.pi/agent/odoo.json`):

```json
{
  "mcpServers": {
    "odoo-sfa": {
      "transport": "streamable-http",
      "url": "http://localhost:8069/mcp",
      "headers": { "Authorization": "Bearer <your-odoo-api-key>" },
      "lifecycle": "lazy"
    }
  }
}
```

Tools then appear in Pi as `mcp__odoo-sfa__<tool>`.
