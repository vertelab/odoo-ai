/** @odoo-module */

import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { Component } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";

/**
 * Systray button that launches an AI Quest from the current form view.
 *
 * Inspired by Odoo Enterprise ai/static/src/web/systray_action.js.
 * When clicked from a form view, it sends the current record's data
 * (model, ID, field values) as context to the AI Quest session.
 *
 * The context flows:
 * 1. Frontend reads model.root.data (all field values) + model.root.fields
 * 2. Calls a server endpoint to create a Quest session with record context
 * 3. The session's set_context_record() serializes fields + chatter
 * 4. The Quest's _extra_context() injects it into the AI system prompt
 */
export class QuestSystray extends Component {
    static template = "ai_agent_context.QuestSystray";
    static props = {};

    setup() {
        super.setup();
        this.orm = useService("orm");
        this.action = useService("action");
        this.bus = this.env.bus;
    }

    /**
     * Get the current form view's model and record data.
     * Uses the same bus pattern as Odoo Enterprise to communicate
     * with the FormController.
     */
    async getCurrentViewInfo() {
        return new Promise((resolve) => {
            const listener = ({ detail }) => {
                this.bus.removeEventListener(
                    "AI_QUEST.SEND_MODEL_DETAILS", listener
                );
                clearTimeout(timeout);
                resolve(detail);
            };
            const timeout = setTimeout(() => {
                this.bus.removeEventListener(
                    "AI_QUEST.SEND_MODEL_DETAILS", listener
                );
                resolve(null);
            }, 150);
            this.bus.addEventListener(
                "AI_QUEST.SEND_MODEL_DETAILS", listener
            );
            this.bus.trigger("AI_QUEST.REQUEST_MODEL_DETAILS");
        });
    }

    /**
     * Main click handler. Gets current form record data,
     * opens the Quest selector, and creates a session with context.
     */
    async onClickLaunchQuest() {
        const currentController = this.action.currentController;
        const viewType = currentController?.view?.type;

        if (viewType !== "form") {
            // Not in a form view - launch without record context
            this._openQuestSelector();
            return;
        }

        const model = await this.getCurrentViewInfo();
        if (!model || !model.root) {
            this._openQuestSelector();
            return;
        }

        // Force save to ensure record exists and chatter is synced
        if (model.root.isDirty && model.root.isDirty()) {
            const saved = await model.root.save();
            if (!saved) return;
        }

        // Get quests available for this model
        const resModel = model.root.resModel;
        const resId = model.root.resId;
        
        this._openQuestSelector({
            resModel: resModel,
            resId: resId,
            recordName: model.root.data.display_name || 
                        model.root.data.name || 
                        `${resModel} #${resId}`,
        });
    }

    /**
     * Open a dialog to select which Quest to launch.
     * Passes the record context so the selected Quest gets it.
     */
    async _openQuestSelector(contextInfo = null) {
        if (contextInfo) {
            // Store context info for the Quest session creation
            const context = {
                default_context_record_model: contextInfo.resModel,
                default_context_record_id: contextInfo.resId,
            };

            this.action.doAction({
                type: "ir.actions.act_window",
                name: contextInfo.recordName 
                    ? _t("Launch Quest for: %s", contextInfo.recordName)
                    : _t("Launch Quest"),
                res_model: "ai.quest",
                views: [[false, "list"], [false, "form"]],
                target: "current",
                context: context,
            });
        } else {
            // No record context - standard quest list
            this.action.doAction({
                type: "ir.actions.act_window",
                name: _t("AI Quests"),
                res_model: "ai.quest",
                views: [[false, "list"], [false, "form"]],
                target: "current",
            });
        }
    }
}

registry
    .category("systray")
    .add("ai_agent_context.QuestSystray", {
        Component: QuestSystray,
    }, { sequence: 31 });
