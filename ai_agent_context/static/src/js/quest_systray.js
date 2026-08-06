/** @odoo-module */

import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { Component, useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { rpc } from "@web/core/network/rpc";
import { useBus } from "@web/core/utils/hooks";

/**
 * Systray button that launches an AI Quest from the current form view
 * with automatic record context injection.
 *
 * Flow:
 * 1. User clicks systray button
 * 2. Frontend reads current model.root (all field values + metadata)
 * 3. Opens a dialog to select which Quest to run
 * 4. Calls /ai_agent_context/launch_quest with record context
 * 5. Backend creates channel + session, injects context, runs quest
 */
export class QuestSystray extends Component {
    static template = "ai_agent_context.QuestSystray";
    static props = {};

    setup() {
        super.setup();
        this.orm = useService("orm");
        this.action = useService("action");
        this.dialog = useService("dialog");
        this.bus = this.env.bus;
        this.state = useState({
            loading: false,
            availableQuests: [],
        });
    }

    // ── View info gathering ─────────────────────────────────────────

    /**
     * Get the current form view's model and all record data.
     * Communicates with FormController via the bus pattern.
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
            }, 200);
            this.bus.addEventListener(
                "AI_QUEST.SEND_MODEL_DETAILS", listener
            );
            this.bus.trigger("AI_QUEST.REQUEST_MODEL_DETAILS");
        });
    }

    /**
     * Extract record data from the model object returned by
     * FormController. Includes all field values, model name, and ID.
     */
    _extractRecordData(model) {
        if (!model || !model.root) {
            return null;
        }

        const root = model.root;
        return {
            model: root.resModel,
            resId: root.resId,
            viewType: "form",
            recordData: root.data
                ? JSON.parse(JSON.stringify(root.data)) // deep copy
                : null,
            displayName: root.data?.display_name
                || root.data?.name
                || `${root.resModel} #${root.resId}`,
            isDirty: root.isDirty ? root.isDirty() : false,
        };
    }

    // ── Systray click handler ────────────────────────────────────────

    async onClickLaunchQuest() {
        if (this.state.loading) return;

        const currentController = this.action.currentController;
        const viewType = currentController?.view?.type;

        if (viewType !== "form") {
            // Not in a form view — open quest list without context
            this._openQuestList();
            return;
        }

        this.state.loading = true;
        try {
            const model = await this.getCurrentViewInfo();
            const recordInfo = this._extractRecordData(model);

            if (!recordInfo || !recordInfo.model || !recordInfo.resId) {
                // No valid record — open quest list without context
                this._openQuestList();
                return;
            }

            // Force save if dirty to ensure record exists
            if (recordInfo.isDirty) {
                try {
                    await model.root.save();
                } catch (e) {
                    // Record might be unsavable — proceed with what we have
                    console.warn("Could not save record before launching quest:", e);
                }
            }

            // Fetch available quests
            const quests = await this._fetchAvailableQuests(recordInfo.model);
            if (!quests || quests.length === 0) {
                // No quests available for this model
                this._openQuestList();
                return;
            }

            // Show quest selector dialog
            this._showQuestSelector(quests, recordInfo);

        } catch (error) {
            console.error("Failed to launch AI quest:", error);
            this._openQuestList();
        } finally {
            this.state.loading = false;
        }
    }

    // ── Quest selection ──────────────────────────────────────────────

    async _fetchAvailableQuests(model) {
        try {
            const result = await this.orm.searchRead(
                "ai.quest",
                [["active", "=", true]],
                ["id", "name", "description", "context_injection_enabled"],
                { limit: 20 }
            );
            return result;
        } catch (e) {
            console.warn("Failed to fetch quests:", e);
            return [];
        }
    }

    _showQuestSelector(quests, recordInfo) {
        const QuestSelectorDialog = this._createQuestSelectorDialog(
            quests, recordInfo
        );
        this.dialog.add(QuestSelectorDialog, {
            quests: quests,
            recordInfo: recordInfo,
            onSelect: this._onQuestSelected.bind(this),
            onCancel: () => {},
        });
    }

    _createQuestSelectorDialog(quests, recordInfo) {
        const self = this;

        return class extends Component {
            static template = "ai_agent_context.QuestSelectorDialog";
            static props = {
                quests: Array,
                recordInfo: Object,
                onSelect: Function,
                onCancel: Function,
            };

            setup() {
                super.setup();
                this.state = useState({
                    selectedQuestId: null,
                    launching: false,
                });
            }

            selectQuest(questId) {
                this.state.selectedQuestId = questId;
            }

            async launch() {
                if (!this.state.selectedQuestId || this.state.launching) return;
                this.state.launching = true;
                try {
                    await this.props.onSelect(
                        this.state.selectedQuestId,
                        this.props.recordInfo
                    );
                } finally {
                    this.state.launching = false;
                }
            }
        };
    }

    async _onQuestSelected(questId, recordInfo) {
        try {
            const result = await rpc("/ai_agent_context/launch_quest", {
                quest_id: questId,
                model: recordInfo.model,
                res_id: recordInfo.resId,
                view_type: recordInfo.viewType,
                record_data: recordInfo.recordData,
            });

            if (result.success && result.channel_id) {
                // Open the discuss channel with the quest
                this.action.doAction({
                    type: "ir.actions.act_window",
                    res_model: "discuss.channel",
                    res_id: result.channel_id,
                    views: [[false, "form"]],
                    target: "current",
                    name: _t("AI Quest: %s", recordInfo.displayName),
                });
            } else {
                console.error("Quest launch failed:", result.error);
            }
        } catch (error) {
            console.error("Failed to launch quest:", error);
        }
    }

    // ── Fallback: open quest list ──────────────────────────────────

    _openQuestList() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: _t("AI Quests"),
            res_model: "ai.quest",
            views: [[false, "list"], [false, "form"]],
            target: "current",
        });
    }
}

// ── Registration ──────────────────────────────────────────────────

registry
    .category("systray")
    .add("ai_agent_context.QuestSystray", {
        Component: QuestSystray,
    }, { sequence: 31 });
