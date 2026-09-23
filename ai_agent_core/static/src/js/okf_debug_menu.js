/** @odoo-module **/
/**
 * OKF — debug-menyns post (okf-mixin F3b, reviderad 2026-09-23).
 *
 * VARFÖR EN FRONTEND-KOMPONENT OCH INTE EN SERVER-ACTION:
 *
 * Den första designen band en `ir.actions.server` via `binding_model_id`
 * och `groups_id = base.group_no_one` — mönstret såg ut som Meta data och
 * Data. Det fungerar inte: Odoo 18:s skalbagge byggs av `debugRegistry`
 * (kategorierna "view", "form", "record", "security", "testing", "tools"),
 * och INGEN av dem läser `actionMenus`/`toolbar`.
 *
 * Mätt på social 2026-09-23:
 *   get_views(crm.lead, form, toolbar=True) → [341, 350, 3682]  ← servern skickar OKF
 *   skalbaggen                              → ingen OKF-post
 *
 * Servern gjorde alltså rätt; frontend renderade den aldrig. Server-actions
 * hamnar i CogMenu (kugghjulet) och bara i list-vyer på små skärmar.
 *
 * Denna komponent registrerar OKF i samma registry som Meta data använder,
 * så den hamnar i skalbaggen på samma plats och med samma mekanism.
 *
 * Åtgärden (att öppna postens OKF-fält) ligger kvar i backend:
 * `ai.okf.mixin.action_open_okf()`.
 */

import { registry } from "@web/core/registry";
import { _t } from "@web/core/l10n/translation";

export function okfDebugItem({ component, env }) {
    const { resId, resModel } = component.model?.config || {};
    // `resId` kan vara en sträng beroende på vy — Owl kräver tal i
    // FormController, så vi normaliserar här (FYND 2026-09-23).
    const id = Number(resId);
    if (!id || !resModel) {
        return null; // ingen post — inget att visa
    }
    return {
        type: "item",
        description: _t("OKF"),
        callback: async () => {
            const action = await env.services.orm.call(
                resModel, "action_open_okf", [id]
            );
            if (action) {
                env.services.action.doAction(action);
            }
        },
        sequence: 130, // efter Metadata (110) och Data (120)
        section: "record",
    };
}

registry.category("debug").category("form").add("okfDebugItem", okfDebugItem);
