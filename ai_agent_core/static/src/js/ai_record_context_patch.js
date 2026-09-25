/** @odoo-module **/
/**
 * AI record context — automatisk vy-kontext för DM/kanal-chatten.
 *
 * PROBLEM
 * =======
 * En DM/kanal är definitionsmässigt INGEN record: den är inte en form-
 * eller list-vy. Men användaren står ofta i en vy när hen skriver i
 * chatten och förväntar sig samma beteende som en server action — där
 * Odoo automatiskt injicerar `records` (active_id/active_ids) från vyn.
 *
 * Discuss-klienten skickar bara texten. `params.context` byggs ur
 * `user.context` (session.user_context), som innehåller lang/tz/uid —
 * INTE active_model/active_id. Därför var env.context alltid tom i
 * DM-requesten och `_detect_record()` hittade aldrig någon record.
 *
 * LÖSNING (A1)
 * ============
 * Vi patchar Thread.post() (discuss-chattens meddelandepostning) och
 * injicerar aktuell vy-kontext i `extraData.context`. Den följer med i
 * `params.context` till /mail/message/post, fångas upp av
 * discuss.channel._capture_ai_context() och lagras på kanalen.
 *
 * Kontexten läses vid VARJE prompt (patchen kör per post) men ligger
 * kvar på kanalen så länge användaren står i samma vy.
 *
 * Källor för aktuell vy (i prioritetsordning):
 *   1. List-vy med förkryssade rader → active_ids (markering)
 *   2. Form-vy → active_id (singular)
 *   3. Ingen vy (t.ex. Discuss-fliken) → ingen kontext, rör inte kanalen
 *
 * Ingen ny systray-knapp: meddelande-systrayn finns redan, och detta
 * sker helt automatiskt när användaren postar.
 */

import { Thread } from "@mail/core/common/thread_model";
import { patch } from "@web/core/utils/patch";

/**
 * Läs aktuell vy-kontext ur action-tjänsten.
 *
 * Returnerar {model, res_id, res_ids, view_type} eller null.
 */
function getCurrentViewContext(env) {
    try {
        const action = env.services?.action;
        const controller = action?.currentController;
        if (!controller) {
            return null;
        }
        const view = controller.view;
        const viewType = view?.type;
        const props = controller.props || {};
        const resModel = props.resModel || controller.props?.resModel;

        // ── 1. List-vy: förkryssade rader ──
        if (viewType === "list") {
            const resIds = controller.model?.root?.resIds
                || controller.model?.root?.records?.map((r) => r.resId)
                || [];
            if (resModel && resIds.length > 1) {
                return {
                    model: resModel,
                    res_ids: resIds,
                    view_type: "list",
                };
            }
            // En rad markerad i list-vyn → singular nedan.
            if (resModel && resIds.length === 1) {
                return {
                    model: resModel,
                    res_id: resIds[0],
                    view_type: "list",
                };
            }
            return null;
        }

        // ── 2. Form-vy: öppnad record ──
        if (viewType === "form") {
            const root = controller.model?.root;
            const resId = root?.resId;
            const model = root?.resModel || resModel;
            if (model && resId) {
                return {
                    model: model,
                    res_id: resId,
                    view_type: "form",
                };
            }
            return null;
        }

        // ── 3. Kanban (Odoo 19+): markerade kort ──
        if (viewType === "kanban") {
            const resIds = controller.model?.root?.resIds || [];
            if (resModel && resIds.length > 1) {
                return {
                    model: resModel,
                    res_ids: resIds,
                    view_type: "kanban",
                };
            }
            if (resModel && resIds.length === 1) {
                return {
                    model: resModel,
                    res_id: resIds[0],
                    view_type: "kanban",
                };
            }
            return null;
        }

        return null;
    } catch (e) {
        // Kontexten får aldrig fälla en chatt-tur.
        console.debug("ai-record-context: kunde inte läsa vy-kontext", e);
        return null;
    }
}

patch(Thread.prototype, {
    /**
     * Injicera aktuell vy-kontext i meddelandets context.
     *
     * Körs per post → kontexten läses vid VARJE prompt (krav a).
     * extraData.context slås in i params.context av post(), och
     * backenden läser context_model/context_res_id/context_res_ids.
     */
    async post(body, postData = {}, extraData = {}) {
        try {
            const ctx = getCurrentViewContext(this.store.env);
            if (ctx) {
                extraData.context = {
                    ...(extraData.context || {}),
                    context_model: ctx.model,
                    context_res_id: ctx.res_id || false,
                    context_res_ids: ctx.res_ids
                        ? ctx.res_ids.join(",")
                        : false,
                    view_type: ctx.view_type,
                };
            }
        } catch (e) {
            console.debug("ai-record-context: injektion misslyckades", e);
        }
        return super.post(body, postData, extraData);
    },
});
