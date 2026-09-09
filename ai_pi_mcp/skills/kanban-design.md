# ai_pi_mcp Kanban Design — MCP-driven five-phase dialog for kanban views

> **Odoo 18 only.** `<t t-name="card">` (never `kanban-box`), `invisible="condition"`
> (never `attrs`/`states`).
>
> This is the **ai_pi_mcp-native** equivalent of `/skill:odoo-kanban`. Discovery,
> iteration and testing run **through the ai_pi_mcp MCP tools** against the Odoo
> instance the MCP server is attached to (live `ir.ui.view` kanban arch edits, no
> module reinstall per iteration). Only an **approved** kanban view is persisted
> into the module's source (`views/*.xml`) and verified with `checkmodule`. The
> file lives in the module so it is versioned with the code; Pi reads it from the
> filesystem.

Five phases:

```
① PLAN    — Discovery + card design dialog (NO CODE)
② BUILD   — Generate kanban XML
③ TEST    — Apply live via MCP tools, iterate
④ PUSH    — Persist approved kanban into module source + checkmodule
⑤ IMPROVE — Metareflection on the skill itself
```

**MCP tools this skill uses**: `describe_model` (fields), `view_list` (existing
kanban), `view_get_arch` / `view_get_merged` (current layout), `view_set_arch`
(apply live), `view_restore` (undo), `create_record` (new `ir.ui.view`),
`action_list` + `action_set_*` (default group-by), `search_read`.

---

## Phase 1: PLAN — Discovery and card design dialog

**No code is written in this phase.** Do not generate XML until the user says
"bygg"/"build"/"generera"/"skapa".

### 1.1 Model resolution

Identify the target model. `/odoo-kanban ai.skill` → `ai.skill`;
"jag vill förfina kanban för ai.agent" → `ai.agent`. If not explicit, ask. If the
user wants several models, run discovery for each independently and present them
all before the dialog.

### 1.2 Discovery via MCP

Fetch the model's fields with `describe_model`. Group them for the user by kind
(identity/status/numeric/relation/rich/date). If the model is unknown,
`describe_model` raises — say so and offer alternatives.

### 1.3 Detect an existing kanban view

`view_list` (model, `type="kanban"`). If one exists, read it with
`view_get_arch` and render the current card layout as text:

```
Fields declared: color, image_128, name, state, ...

Card layout:
┌──────────────────────────────┐
│  [image] Name                │
│  Description (truncated)     │
│  ─────────────────────────── │
│  State badge   v1 · N uses   │
└──────────────────────────────┘
```

Then ask:
> "`<model>` already has a kanban view. Do you want to
> 1. **Modify it** via XPath inheritance,
> 2. **Replace it** (new view, higher priority), or
> 3. **Create a new additional view**?"

### 1.4 Column grouping

If the model has `stage_id` (Many2one to a stage model) suggest grouping into
columns by stage. Otherwise, if there is a Selection/Many2one candidate
(`skill_type`, `state`, `provider_type`, `risk_level`…), suggest grouping by it.
Else suggest a single-column layout.

### 1.5 Card field selection (with widget suggestions)

Recommend defaults and explain why:

- `name` → card title.
- Selection → `badge` (colored).
- Boolean → `boolean_facade` (inline checkbox).
- Integer → `star_rating` (counters) or `plain`.
- Date/Datetime → `remaining_days` (deadlines).
- Many2one (user) → `many2one_avatar`; Many2many → `many2many_tags`.
- **Html → prefer NOT on kanban** (slow). Offer a preview link instead.
- Image → `image`/`background_image`.

### 1.6 Color coding

Ask if cards should be colored by a field. For Selection: positive/active →
`decoration-success`, warning/pending → `decoration-warning`, negative/error →
`decoration-danger`, info → `decoration-info`.

### 1.7 Filters and group-by in the search panel

Ask which filters and groupings should appear. The grouping from §1.4 should be
the default group-by (the kanban `default_group_by`).

### 1.8 Quick create and drag-and-drop

- If the module has a `post_init_hook` creating records programmatically (common
  in `ai_agent_core`), suggest `quick_create="false"`.
- Drag-and-drop between columns requires the grouping field to be write-accessible.

### 1.9 Transition to BUILD

> "When you're ready, say **'bygg'** / **'build'** / **'generera'** and I'll
> create the XML and test it live."

---

## Phase 2: BUILD — XML generation

Generate Odoo 18-compliant kanban XML. Mandatory rules:

- **CARD**: `<t t-name="card">` — never `kanban-box`.
- **ATTRS**: `invisible="condition"` — never `attrs`/`states`.
- **FIELDS**: every field used inside `<templates>` must also appear BEFORE
  `<templates>` in the `<kanban>` tag (including fields only in `t-if`/`t-att-*`).
- **TRANSLATION**: wrap user-visible strings in `_()`.

Template skeleton:

```xml
<record id="<xml_id>" model="ir.ui.view">
    <field name="name"><model>.kanban.<feature></field>
    <field name="model"><model></field>
    <field name="arch" type="xml">
        <kanban class="o_kanban_mobile"
                default_group_by="<group_field>"
                quick_create="true|false"
                color_field="<color_field>">
            <!-- Field declarations -->
            <field name="name"/>
            <field name="<selection_field>" widget="badge"/>
            <field name="<boolean_field>" widget="boolean_facade"/>

            <!-- Optional progressbar -->
            <progressbar field="<field>"
                         colors="{'done': 'success', 'blocked': 'danger'}"
                         sum_field="<numeric_field>"/>

            <templates>
                <t t-name="card">
                    <div class="oe_kanban_card oe_kanban_global_click">
                        <div class="o_kanban_card_header">
                            <strong class="o_kanban_record_title">
                                <field name="name"/>
                            </strong>
                        </div>
                        <div class="o_kanban_card_content">
                            <!-- Fields go here -->
                        </div>
                        <div class="o_kanban_card_footer">
                            <div class="oe_kanban_bottom_left">
                                <field name="<selection_field>" widget="badge"/>
                            </div>
                            <div class="oe_kanban_bottom_right">
                                <field name="<boolean_field>" widget="boolean_facade"/>
                            </div>
                        </div>
                    </div>
                </t>
            </templates>
        </kanban>
    </field>
</record>
```

For **XPath inheritance** of an existing kanban, use `inherit_id` + a fragment
with `<xpath expr="//kanban" …>`, `<xpath expr="//t[@t-name='card']" …>` etc.
New fields used inside the card must be added with `position="inside"` on
`<kanban>` (field declaration). Show the XML and ask before testing.

---

## Phase 3: TEST — apply live via MCP, iterate

- **Existing kanban**: `view_set_arch` with the `view_id` + new arch (auto-backup;
  `view_restore` to undo).
- **New kanban** (no record): `create_record` on `ir.ui.view` (`name`, `model`,
  `type="kanban"`, `arch`), then refine with `view_set_arch` if needed. If the
  kanban must ship in a module, it is often simpler to go straight to Phase 4.

Verify with `view_get_merged` (model + `type="kanban"`) to confirm the effective
arch. Iterate: feedback → adjust XML → `view_set_arch` → re-verify, until the
user approves ("bra"/"godkänd"/"klar").

---

## Phase 4: PUSH — persist approved kanban into module source + checkmodule

1. Capture the final arch (`view_get_arch` or the built XML).
2. Write it into `<module>/views/<model>_kanban.xml` (a `<record model="ir.ui.view">`,
   or an inheritance record with `inherit_id` + xpath). Add the file to the
   manifest `data` list.
3. Test-install so the module still works:
   ```bash
   sudo checkmodule -d <database> -m <module>
   # production: add -D (no demo data)
   sudo grep -E "ERROR|CRITICAL|Traceback" /var/log/odoo/odoo.log | tail -10
   ```
4. Confirm registration: `view_list` (model, `type="kanban"`) shows the view with
   its `xml_id`.
5. Commit (English: `[IMP] <model>: add/update kanban view with <features>`). Do
   not push automatically.

---

## Phase 5: IMPROVE — metareflection

Record learnings (widgets chosen, defaults overridden, edge cases) in
`skills/improvements.md` and offer SKILL.md updates to the user.

---

## Common pitfalls (Odoo 18 kanban)

1. `t-att-class` is JavaScript: `in` checks object keys. Use
   `record.field.raw_value == 'a' || record.field.raw_value == 'b'`, not
   `in ('a','b')`.
2. `d-flex` + `flex-row` together — `flex-row` alone does not set `display: flex`.
3. Web ribbon is cleaner than a manual `<div>`:
   `<widget name="web_ribbon" title="Inactive" bg_color="text-bg-danger" invisible="not active"/>`.
4. Image/avatar aside with fallback: use `o_kanban_aside_full` (95px) for a
   full-height aside; otherwise the aside defaults to 64px.
5. Truncate long text with CSS (`-webkit-line-clamp: 2`), not with field options.
6. Every field in `<templates>` must be declared before `<templates>`.

## Related

- `/skill:odoo-kanban` — original (psql/shell/agent-browser based) methodology.
- `skills/view-design.md` (this module) — all-view-types MCP workflow.
- `/skill:odoo-18`, `/skill:odoo-unified`, `/skill:odoo-vertel-env`.
