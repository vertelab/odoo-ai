/** @odoo-module **/
/**
 * Overflow "+N" öppnar avdelningens medlemslista i kanban
 * (org-goal-integrity D10).
 *
 * PROBLEM
 * =======
 * `many2many_tags_avatar`-widgeten (i `web`) visar högst
 * `visibleItemsLimit` poster — vid fler renderas 9 ansikten följt av en
 * "+N"-bricka (`.o_m2m_avatar_empty`). Brickan har en tooltip som listar de
 * dolda namnen, men INGEN klick-hook i core. På `hr.department` vill vi att
 * klick på "+N" öppnar avdelningens medlemslista i kanban (den befintliga
 * `action_get_ai_staff`).
 *
 * LÖSNING
 * =======
 * Två små patchar:
 *
 *   1. `TagsList` (core, i `web`) får en valfri `onOverflowClick`-prop.
 *      Mallen (`web.TagsList`) binds via XML-inherit i
 *      `many2many_tags_avatar_overflow_patch.xml` — brickan får
 *      `t-on-click` som anropar proppen när den finns.
 *
 *   2. `Many2ManyTagsAvatarField` skickar `onOverflowClick` ned till
 *      `TagsList` ENDAST när fältets record är en `hr.department`. Övriga
 *      användningar av widgeten (t.ex. `coworker_ids` på ai.org.goal) är
 *      oförändrade — ingen klick-hook, exakt core-beteende.
 *
 * Ingen ny action: `hr.department.action_get_ai_staff` finns redan och
 * öppnar `ai.coworker` i kanban,list,form.
 *
 * Källor: `web/static/src/core/tags_list/tags_list.{js,xml}`,
 * `web/static/src/views/fields/many2many_tags_avatar/...`.
 */

import { TagsList } from "@web/core/tags_list/tags_list";
import { Many2ManyTagsAvatarField } from "@web/views/fields/many2many_tags_avatar/many2many_tags_avatar_field";
import { patch } from "@web/core/utils/patch";

// ── 1. TagsList: valfri klick-hook på overflow-brickan ────────────────
// Proppen är optional; saknas den är beteendet exakt som i core.
TagsList.props = {
    ...TagsList.props,
    onOverflowClick: { type: Function, optional: true },
};
// Byt mall till vår inherit (lägger t-on-click på brickan).
TagsList.template = "ai_agent_core.Many2ManyTagsAvatarOverflowPatch";

patch(TagsList.prototype, {
    /**
     * Anropas av mallen när "+N"-brickan klickas. No-op utan propp.
     */
    onOverflowBadgeClick(ev) {
        if (this.props.onOverflowClick) {
            ev.stopPropagation();
            ev.preventDefault();
            this.props.onOverflowClick(ev);
        }
    },
});

// ── 2. Many2ManyTagsAvatarField: skicka hooken för hr.department ──────
patch(Many2ManyTagsAvatarField.prototype, {
    /**
     * Klick på "+N" → avdelningens medlemslista i kanban.
     *
     * Returnerar undefined för alla andra modeller, så `TagsList` behåller
     * core-beteendet (ingen klick-hook).
     */
    get onOverflowClick() {
        const record = this.props.record;
        if (!record || record.resModel !== "hr.department") {
            return undefined;
        }
        return async () => {
            const action = await this.orm.call(
                "hr.department",
                "action_get_ai_staff",
                [[record.resId]],
            );
            if (action) {
                this.env.services.action.doAction(action);
            }
        };
    },
});
