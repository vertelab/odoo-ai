/** @odoo-module **/
/**
 * AI Context — systray-knapp.
 *
 * Ported from ai_agent_context/static/src/js/quest_systray.js (2026-11-16).
 *
 * SYFTE
 * =====
 * Användaren står i en helpdesk-ticket (form) eller har markerat rader i en
 * list. Ett klick ska ge en DM med en AI-medarbetare där recorden är
 * kontext — så hen kan ställa frågor om den.
 *
 * Skillnad mot powerbox (/ai/powerbox/*)
 * =====================================
 * Powerbox är ett slash-kommando INUTI ett textfält: välj quest → svaret
 * skrivs i fältet. Inline-textgenerering, inget samtal.
 *
 * Den här knappen startar ett SAMTAL. Recorden följer med som kontext så
 * följdfrågor fungerar.
 *
 * Skillnad mot ai_record_context_patch.js
 * =====================================
 * Patchen fångar vyn automatiskt vid varje meddelande i en REDAN öppen
 * kanal. Knappen skapar kanalen (första turen) och kan byta record.
 */

import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { Component, useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { rpc } from "@web/core/network/rpc";

export class AIContextSystray extends Component {
    static template = "ai_agent_core.AIContextSystray";
    static props = {};

    setup() {
        super.setup();
        this.orm = useService("orm");
        this.action = useService("action");
        this.dialog = useService("dialog");
        this.bus = this.env.bus;
        this.state = useState({ loading: false });
    }

    // ── Läs aktuell vy ───────────────────────────────────────────────

    /**
     * Fråga FormController (via bus) om dess model-objekt.
     *
     * Timeout 200 ms: står användaren inte i en form svarar ingen, och då
     * fortsätter vi utan kontext i stället för att hänga.
     */
    async getCurrentViewInfo() {
        return new Promise((resolve) => {
            const listener = ({ detail }) => {
                this.bus.removeEventListener(
                    "AI_CONTEXT.SEND_MODEL_DETAILS", listener
                );
                clearTimeout(timeout);
                resolve(detail);
            };
            const timeout = setTimeout(() => {
                this.bus.removeEventListener(
                    "AI_CONTEXT.SEND_MODEL_DETAILS", listener
                );
                resolve(null);
            }, 200);
            this.bus.addEventListener(
                "AI_CONTEXT.SEND_MODEL_DETAILS", listener
            );
            this.bus.trigger("AI_CONTEXT.REQUEST_MODEL_DETAILS");
        });
    }

    /** Form-vy: en record. List-vy: den markerade raden/raderna. */
    _extractRecordInfo(model) {
        if (!model || !model.root) {
            return null;
        }
        const root = model.root;
        if (!root.resModel) {
            return null;
        }
        const resId = root.resId;
        const resIds = root.resIds || (resId ? [resId] : []);
        if (!resId && !resIds.length) {
            return null;
        }
        const data = root.data
            ? JSON.parse(JSON.stringify(root.data))
            : null;
        return {
            model: root.resModel,
            resId: resId || false,
            resIds: resIds,
            viewType: "form",
            recordData: data,
            displayName:
                data?.display_name ||
                data?.name ||
                `${root.resModel} #${resId}`,
            isDirty: root.isDirty ? root.isDirty() : false,
        };
    }

    // ── Klick ────────────────────────────────────────────────────────

    async onClick() {
        if (this.state.loading) return;

        const controller = this.action.currentController;
        const viewType = controller?.view?.type;

        this.state.loading = true;
        try {
            let recordInfo = null;
            if (viewType === "form") {
                const model = await this.getCurrentViewInfo();
                recordInfo = this._extractRecordInfo(model);
                // Osparade ändringar ska följa med — spara först.
                if (recordInfo?.isDirty && model?.root?.save) {
                    try {
                        await model.root.save();
                    } catch (e) {
                        console.warn("ai-context: kunde inte spara:", e);
                    }
                }
            }

            const coworkers = await this._fetchCoworkers();
            if (!coworkers.length) {
                this._openCoworkerList();
                return;
            }
            this._showSelector(coworkers, recordInfo);
        } catch (error) {
            console.error("ai-context: klick misslyckades:", error);
            this._openCoworkerList();
        } finally {
            this.state.loading = false;
        }
    }

    async _fetchCoworkers() {
        try {
            return await this.orm.searchRead(
                "ai.coworker",
                [["active", "=", true]],
                ["id", "name", "description"],
                { limit: 30 }
            );
        } catch (e) {
            console.warn("ai-context: kunde inte hämta coworkers:", e);
            return [];
        }
    }

    _showSelector(coworkers, recordInfo) {
        const parent = this;
        this.dialog.add(
            class extends Component {
                static template = "ai_agent_core.AIContextSelector";
                static props = {
                    coworkers: Array,
                    recordInfo: Object,
                    onSelect: Function,
                    onCancel: Function,
                };

                setup() {
                    super.setup();
                    this.state = useState({
                        selectedId: null,
                        launching: false,
                    });
                }

                select(id) {
                    this.state.selectedId = id;
                }

                async launch() {
                    if (!this.state.selectedId || this.state.launching) return;
                    this.state.launching = true;
                    try {
                        await this.props.onSelect(
                            this.state.selectedId, this.props.recordInfo
                        );
                    } finally {
                        this.state.launching = false;
                    }
                }
            },
            {
                coworkers,
                recordInfo,
                onSelect: parent._onSelected.bind(parent),
                onCancel: () => {},
            }
        );
    }

    async _onSelected(coworkerId, recordInfo) {
        const params = {
            coworker_id: coworkerId,
            model: recordInfo?.model || false,
            res_id: recordInfo?.resId || false,
            res_ids: recordInfo?.resIds || false,
            view_type: recordInfo?.viewType || false,
            record_data: recordInfo?.recordData || false,
        };
        try {
            const result = await rpc("/ai/context/launch", params);
            if (result.success && result.channel_id) {
                this.action.doAction({
                    type: "ir.actions.act_window",
                    res_model: "discuss.channel",
                    res_id: result.channel_id,
                    views: [[false, "form"]],
                    target: "current",
                    name: recordInfo
                        ? _t("AI: %s", recordInfo.displayName)
                        : _t("AI Chat"),
                });
            } else {
                console.error("ai-context: launch misslyckades:", result.error);
            }
        } catch (error) {
            console.error("ai-context: launch kastade:", error);
        }
    }

    _openCoworkerList() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: _t("AI Coworkers"),
            res_model: "ai.coworker",
            views: [[false, "list"], [false, "form"]],
            target: "current",
        });
    }
}

registry
    .category("systray")
    .add("ai_agent_core.AIContextSystray", {
        Component: AIContextSystray,
    }, { sequence: 31 });
