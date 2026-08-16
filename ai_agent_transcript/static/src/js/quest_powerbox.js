/** @odoo-module **/

/**
 * Powerbox Quest Integration for ai_agent
 * ---------------------------------------
 *
 * Ported from Odoo Enterprise patterns:
 * - ai/static/src/editor/plugins/chatgpt_plugin.js (ChatGPTPlugin powerbox resources)
 * - ai/static/src/core/html_editor/prompt_plugin.js (PromptPlugin powerbox integration)
 * - ai/models/discuss_channel.py (create_ai_draft_channel)
 *
 * This module patches the existing ai_agent quest dialog to support
 * powerbox-style invocation from record form views. When a user opens
 * the AI assistant from a record, the system:
 *
 * 1. Captures the current view context (model, record ID)
 * 2. Finds the matching ai.composer for this interface point
 * 3. Creates a quest session with transcript context
 * 4. Passes record data, chatter history, and quick prompts to the UI
 */

import { registry } from "@web/core/registry";
import { rpc } from "@web/core/network/rpc";
import { user } from "@web/core/user";

const powerboxService = {
    dependencies: ["notification", "action"],
    start(env, { notification, action }) {
        return {
            /**
             * Open a powerbox quest from the given interface point.
             *
             * @param {Object} params
             * @param {string} params.interfaceKey - The interface point identifier
             * @param {string} [params.recordModel] - Current model
             * @param {number} [params.recordId] - Current record ID
             * @param {string} [params.textSelection] - Selected text (for rewrite)
             * @param {Object} [params.frontendInfo] - Frontend record data
             * @returns {Promise<Object>} Session info with quest details and prompts
             */
            async openPowerboxQuest(params) {
                const { interfaceKey, recordModel, recordId,
                        textSelection, frontendInfo } = params;

                if (!user.isInternalUser) {
                    notification.add(
                        "AI features are only available for internal users.",
                        { type: "warning" }
                    );
                    return null;
                }

                try {
                    // 1. Hitta composer via ai.composer (transcript-modulen)
                    const composer = await rpc("/web/dataset/call_kw", {
                        model: "ai.composer",
                        method: "find_composer",
                        args: [[]],
                        kwargs: {
                            interface_key: interfaceKey,
                            model_name: recordModel,
                        },
                    });

                    if (!composer || !composer.coworker_id) {
                        notification.add(
                            "Ingen AI-composer hittades för denna interface-punkt.",
                            { type: "warning" }
                        );
                        return null;
                    }

                    // 2. Kör powerbox via core-endpoint (/ai/powerbox/run)
                    const run = await fetch("/ai/powerbox/run", {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({
                            coworker_id: composer.coworker_id[0],
                            text: textSelection || "Hjälp mig med detta rekord",
                            model: recordModel,
                            res_id: recordId,
                        }),
                    });
                    const data = await run.json();
                    return data;
                } catch (error) {
                    console.error("[powerbox] Failed to open quest:", error);
                    notification.add(
                        error.data?.message || "Failed to start AI session.",
                        { type: "danger" }
                    );
                    return null;
                }
            },

            /**
             * Launch AI chat from a record form view.
             * Used by the systray Quest button and form controller patches.
             *
             * @param {Object} context
             * @param {string} context.interfaceKey
             * @param {string} context.recordModel
             * @param {number} context.recordId
             * @param {Object} [context.recordData]
             * @returns {Promise<Object>}
             */
            async launchFromRecord(context) {
                return this.openPowerboxQuest({
                    interfaceKey: context.interfaceKey || "chatter_ai_button",
                    recordModel: context.recordModel,
                    recordId: context.recordId,
                    frontendInfo: context.recordData,
                });
            },

            /**
             * Launch AI from an HTML field / text editor.
             *
             * @param {Object} context
             * @param {string} context.interfaceKey
             * @param {string} context.recordModel
             * @param {number} context.recordId
             * @param {string} [context.textSelection]
             * @param {Object} [context.recordData]
             * @returns {Promise<Object>}
             */
            async launchFromEditor(context) {
                return this.openPowerboxQuest({
                    interfaceKey: context.interfaceKey || "html_field_record",
                    recordModel: context.recordModel,
                    recordId: context.recordId,
                    textSelection: context.textSelection,
                    frontendInfo: context.recordData,
                });
            },
        };
    },
};

registry.category("services").add("ai_powerbox", powerboxService);
