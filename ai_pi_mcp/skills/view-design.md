# ai_pi_mcp View Design — MCP-driven five-phase dialog for all view types

> **Odoo 18 only.** Same conventions as `/skill:odoo-view`: `<list>` not `<tree>`,
> `<t t-name="card">` not `kanban-box`, `invisible="condition"` not `attrs`/`states`,
> `.with_user(user)` not `.sudo(user)`.
>
> This skill is the **ai_pi_mcp-native** equivalent of `/skill:odoo-view`. The
> difference: discovery, iteration and testing run **through the ai_pi_mcp MCP
> tools** against the Odoo instance the MCP server is attached to (live
> `ir.ui.view` edits, no module reinstall or Odoo restart per iteration). Only
> when a view is **approved** is it persisted into the module's source
> (`views/*.xml`) and verified with `checkmodule`. This file lives in the module
> so it is versioned with the code; Pi reads it from the filesystem.

## Working rules (MCP live edits → explicit go-ahead → source/checkmodule)

These rules govern the whole workflow and apply on every minion that has
`odoo-ai` on the filesystem (they live in this module, not in a local memory):

- **a) While working over MCP, make LIVE edits directly in the view**
  (`view_set_arch` on `ir.ui.view`) — no source-code edits, no module
  reinstall, no Odoo restart per iteration. This stays true until the user
  gives an **explicit go-ahead** to either (1) **save the changes into the
  module's source** (`views/*.xml`) or (2) **revert to the prior state**.
- **b) When the module is to be updated, ALWAYS ASK before running
  `checkmodule`.** Once the user has given the go-ahead, it is fine to fix
  errors and re-run `checkmodule` until the module installs cleanly.
- **c) `checkmodule` can run in single-user mode (Odoo is stopped) or
  multi-user mode (on a different port than the Odoo server).** ASK which
  mode to use.

---

Five phases (same as odoo-view):

```
① PLAN    — Discovery + view-type-specific dialog (NO CODE)
② BUILD   — Generate Odoo 18 XML
③ TEST    — Apply live via MCP tools, iterate
④ PUSH    — Persist approved view into module source + checkmodule
⑤ IMPROVE — Metareflection on the skill itself
```

**MCP tool cheat-sheet used by this skill** (all via the ai_pi_mcp MCP server):

| Purpose | Tool | Notes |
|---------|------|-------|
| Model metadata | `describe_model` | fields/type/readonly/store/relation/selection |
| Existing views | `view_list` | views for a model, optional `type` filter |
| View arch | `view_get_arch` | raw arch of one view by id |
| Effective arch | `view_get_merged` | what the user sees after inheritance |
| Apply arch live | `view_set_arch` | backups previous arch (view_restore to undo) |
| Undo | `view_restore` | restore last backup of a view |
| New view record | `create_record` | on `ir.ui.view` (then `view_set_arch`) |
| Actions | `action_list` | act_window actions for a model |
| Tweak action | `action_set_domain` / `action_set_view_mode` / `action_set_order` | |
| Records | `search_read` / `count` / `read_records` | data lookups |
| Module state | `module_list` | confirm a module is installed |

---

## Phase 1: PLAN — Discovery and Design Dialog

**No code is written in this phase.** Do not generate XML until the user says
"bygg", "build", "generera" or "skapa".

### 1.1 Trigger and Model/View resolution

Identify the target model and view type from the user's message:

- "designa en form för ai.agent" → model `ai.agent`, type `form` (default)
- "designa en listvy för project.task" → model `project.task`, type `list`
- "bygg en pivot för account.move" → model `account.move`, type `pivot`

If the model or type is not explicit, ask. If the user asks which view types are
available, present the inventory in §1.2.

### 1.2 View type selection + availability

Default type is `form`. Offer the inventory grouped by source:

| Group | Types |
|-------|-------|
| Core | form, list, kanban, search, pivot, graph, calendar |
| Mail | activity |
| web_hierarchy | hierarchy |
| OCA | timeline, quick_start_screen |
| Vertel CE | gantt, grid, map, cohort, bcg_matrix, year_wheel, swot |

For non-core types, confirm the backing module is installed on the MCP target
before designing: `module_list` with `search` = the module name (e.g.
`web_timeline`, `web_gantt_ce`, `web_hierarchy`). If not installed, tell the
user and offer an alternative type (or `module_install` if they want it).

### 1.3 Discovery via MCP

Fetch the model's fields with `describe_model` (model = target). Group the
returned fields for the user by kind:

- **Identity** (Char): `name`, `version`, `reference`…
- **Status** (Selection/Boolean): `state`, `active`, `stage_id`…
- **Numeric** (Integer/Float): `amount`, `count`, `sequence`…
- **Relation** (Many2one/Many2many/One2many): `partner_id`, `tag_ids`, `line_ids`…
- **Rich** (Text/Html): `description`, `notes`…
- **Date** (Date/Datetime): `date_start`, `date_deadline`…

If the model is unknown, `describe_model` raises — tell the user the model does
not exist on this instance.

### 1.4 Detect an existing view

Run `view_list` (model, optional `type`). If a view of the target type exists,
read its arch with `view_get_arch` (or `view_get_merged` for the effective
layout) and present a short text rendering. Then ask:

> "`<model>` already has a `<type>` view. Do you want to
> 1. **Modify it** via XPath inheritance,
> 2. **Replace it** (new view, higher priority), or
> 3. **Create a new additional view**?"

For a **new** view there is often no `ir.ui.view` record yet — that record is
created in Phase 3 (TEST) via `create_record`.

### 1.5 Dialog per view type

Walk the user through the type-specific questions from `/skill:odoo-view`
(§1.9–§1.18). The MCP tools change **how** you discover data, never **what** the
dialog asks. For the common types, the dimensions are:

- **form**: header workflow (statusbar/buttons) → button box → `oe_title` →
  groups (2-col) → notebook (tabs) → inline x2many → chatter (after `</sheet>`).
- **list**: editable?, columns (+optional), decorations, sums, widgets, sticky,
  multi-edit.
- **kanban**: see the companion `skills/kanban-design.md` in this module.
- **search**: searchable fields, filters, date range, group-by.
- **pivot**: rows, columns, measures, linking.
- **graph**: chart type, X/Y, series, stacked, interval.
- **calendar**: date_start/stop(±duration), title, color, quick_add, all_day, mode.
- Others (activity/hierarchy/timeline/vertel CE): follow the dimension lists in
  `/skill:odoo-view` §1.15–§1.18.

### 1.6 Widget recommendations

Use the per-view-type widget tables from `/skill:odoo-view` §1.19 (form/list/
kanban/OCA). Always explain *why* you suggest a widget, not just which.

### 1.7 Transition to BUILD

> "I have a clear picture. When you're ready, say **'bygg'** / **'build'** /
> **'generera'** / **'skapa'** and I'll create the XML and test it live."

Do NOT emit XML before the user explicitly asks.

---

## Phase 2: BUILD — XML generation

Generate Odoo 18-compliant XML per the mandatory rules:

- **LIST**: `<list>` not `<tree>` (standalone and inline x2many).
- **CARD**: `<t t-name="card">` not `kanban-box`.
- **ATTRS**: `invisible="condition"` never `attrs`/`states`.
- **FIELDS**: every field used inside `<templates>` also declared before
  `<templates>` in kanban.
- **TRANSLATION**: wrap user-visible strings in `_()`; commit messages English.

Show the XML and ask for approval before testing.

---

## Phase 3: TEST — apply live via MCP, iterate

This is where the MCP workflow differs from `/skill:odoo-view`. Instead of
writing to module source + `checkmodule` for every tweak (slow, restarts Odoo),
apply the arch **live to the running instance** and iterate:

### 3.1 Apply the arch live

- **Existing view** (modify/replace): `view_set_arch` with the target `view_id`
  and the new arch. The tool backs up the previous arch automatically.
- **New view** (no record yet): first `create_record` on `ir.ui.view` with the
  metadata fields — `name`, `model`, `type`, `arch` (the XML), and for an
  inheritance view `inherit_id` + `mode="extension"`. Then, if you only set a
  stub arch at creation, refine it with `view_set_arch`.

> Creating an `ir.ui.view` record directly is a **write** on a core model. Only
> do it when there is genuinely no existing record to extend. For a brand-new
> view that must ship in a module, it is often simpler to skip live creation and
> go straight to Phase 4 (module source + checkmodule). Choose based on whether
> the user wants to iterate live first.

### 3.2 Verify

- Re-read with `view_get_merged` (model + type) to confirm the effective arch
  renders as intended (all inheritance applied).
- If the arch is malformed, `view_set_arch` raises — fix the XML and retry.
- If a live change is wrong, undo with `view_restore` (same `view_id`).

### 3.3 Iterate

Present the result, take feedback, adjust the XML, re-apply with `view_set_arch`,
re-verify. Loop until the user says "bra" / "good" / "godkänd" / "klar".

---

## Phase 4: PUSH — persist approved view into module source + checkmodule

Triggered by the user saying "push"/"pusha"/"commit"/"spara i modulen". This is
the step that satisfies "when approved, update the module's source code and
test-install with checkmodule".

1. **Capture the final arch** (from the live view via `view_get_arch`, or from
   the XML you built).
2. **Write it into the module's source** under `<module>/views/`:
   - New view → `<module>/views/<model>_<type>.xml` with a `<record model="ir.ui.view">`.
   - Inheritance → the same file with `<record model="ir.ui.view">` +
     `<field name="inherit_id" ref="…"/>` + the xpath fragment.
   - Add the file to `<module>/__manifest__.py` `data` list (before any view it
     depends on; parents before children in menu.xml).
   - If the view was only iterated live and must also be the shipped version,
     ensure the live record and the module XML agree (the module XML is the
     source of truth after the next upgrade).
3. **Test-install with checkmodule** so the module still works:
   ```bash
   sudo checkmodule -d <database> -m <module>
   ```
   (no demo data on production: add `-D`). checkmodule stops/starts Odoo and
   re-runs the module update. Watch for errors in the tail of the log:
   ```bash
   sudo grep -E "ERROR|CRITICAL|Traceback" /var/log/odoo/odoo.log | tail -10
   ```
4. **Confirm registration**: after checkmodule, `view_list` (model, type) should
   show the view with its `xml_id` set.
5. **Commit** (English, e.g. `[IMP] <model>: add <type> view with <features>`).
   Do not push automatically — wait for the user.

**Important**: if a live-only view was created in Phase 3 and is *not* meant to
ship, leave it out of module source — live `ir.ui.view` records without a module
`xml_id` are dev scratch and can be removed once superseded by the module view.

---

## Phase 5: IMPROVE — metareflection

After the user confirms the push, reflect: which widgets/patterns were chosen,
which defaults overridden, any issues. Write findings to the module's
`skills/improvements.md` and suggest SKILL.md updates to the user (apply only
with their approval).

---

## Common pitfalls (Odoo 18)

1. `t-att-class` is JavaScript: `in` checks object keys — use `==`/`||`.
2. `d-flex` + `flex-row` must be used together (flex-row alone sets no display).
3. Chatter goes AFTER `</sheet>`, never inside.
4. Kanban: every `<field>` in `<templates>` must be declared before `<templates>`.
5. `<list>` not `<tree>` (standalone and inline x2many).
6. `view_set_arch` validates the XML first and backs up; use `view_restore` to undo.
7. Creating an `ir.ui.view` record is a write on a core model — prefer extending
   an existing record, or ship via module source.

## Related

- `/skill:odoo-view` — original (psql/shell/agent-browser based) methodology.
- `skills/kanban-design.md` (this module) — kanban-specific MCP workflow.
- `/skill:odoo-18`, `/skill:odoo-unified`, `/skill:odoo-vertel-env`.
