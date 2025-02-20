import { _t } from "@web/core/l10n/translation";
import { Plugin } from "@html_editor/plugin";
import { closestElement } from "@html_editor/utils/dom_traversal";
import { QuestPromptDialog } from '@ai_agent/js/components/quest_prompt_dialog';
import { withSequence } from "@html_editor/utils/resource";
import { MAIN_PLUGINS } from "@html_editor/plugin_sets";
import { rpc } from "@web/core/network/rpc";

export class QuestPlugin extends Plugin {
    static id = "quest";
    static dependencies = ["selection", "history", "dom", "sanitize", "dialog"];

    resources = {
        user_commands: [
            {
                id: "openQuestDialog",
                title: _t("AI Quest"),
                description: _t("Generate or transform content with AI Quest."),
                icon: "fa-magic",
                run: this.openDialog.bind(this),
            },
        ],
        powerbox_categories: withSequence(80, { id: "ai_quest", name: _t('AI Quest') }),
        powerbox_items: [
            {
                title: _t("Generic Quest"),
                description: _t("Generic quest to perform operations"),
                categoryId: "ai_quest",
                commandId: "openQuestDialog",
            },
        ]
    };


    async setup() {
        await super.setup();
        // Initialize powerbox items
        const powerbox_items = await this.powerboxQuests();
        if (powerbox_items.length > 0) {
            // Update both resources and notify the component
            this.resources.powerbox_items = powerbox_items;
//            this._resources.powerbox_items.push(...powerbox_items);
            // Trigger a resources update
//            this.updateResources();
        }
        console.log("powerbox_items", this.resources.powerbox_items)
        console.log("state", this)
    }

    async powerboxQuests() {
        try {
            const { resModel, resId } = this.config.getRecordInfo?.() || {};

            const powerbox_quests = await rpc('/web/dataset/call_kw', {
                model: 'ai.quest',
                method: 'search_read',
                args: [[['init_type', '=', 'powerbox']]],
                kwargs: {
                    fields: ['id', 'name', 'sub_description']
                },
            }, { shadow: true });

            if (!Array.isArray(powerbox_quests)) {
                console.error('Unexpected response format:', powerbox_quests);
                return [];
            }

            return powerbox_quests.map(powerbox_quest => ({
                title: _t(powerbox_quest.name),
                description: powerbox_quest.sub_description,
                categoryId: "ai_quest",
                commandId: "openQuestDialog",
            }));
        } catch (error) {
            console.error('Error fetching powerboxes:', error);
            return [];
        }
    }

    openDialog(params = {}) {
        const {resModel, resId } = this.config.getRecordInfo?.() || {}

        const recordInfo = {
            'res_model': resModel,
            'res_id': resId
        }


        const selection = this.dependencies.selection.getEditableSelection();
        const dialogParams = {
            insert: (content) => {
                const insertedNodes = this.dependencies.dom.insert(content);
                this.dependencies.history.addStep();
                // Add a frame around the inserted content to highlight it for 2
                // seconds.
                const start = insertedNodes?.length && closestElement(insertedNodes[0]);
                const end =
                    insertedNodes?.length &&
                    closestElement(insertedNodes[insertedNodes.length - 1]);
                if (start && end) {
                    const divContainer = this.editable.parentElement;
                    let [parent, left, top] = [
                        start.offsetParent,
                        start.offsetLeft,
                        start.offsetTop - start.scrollTop,
                    ];
                    while (parent && !parent.contains(divContainer)) {
                        left += parent.offsetLeft;
                        top += parent.offsetTop - parent.scrollTop;
                        parent = parent.offsetParent;
                    }
                    let [endParent, endTop] = [end.offsetParent, end.offsetTop - end.scrollTop];
                    while (endParent && !endParent.contains(divContainer)) {
                        endTop += endParent.offsetTop - endParent.scrollTop;
                        endParent = endParent.offsetParent;
                    }
                    const div = document.createElement("div");
                    div.classList.add("o-chatgpt-content");
                    const FRAME_PADDING = 3;
                    div.style.left = `${left - FRAME_PADDING}px`;
                    div.style.top = `${top - FRAME_PADDING}px`;
                    div.style.width = `${
                        Math.max(start.offsetWidth, end.offsetWidth) + FRAME_PADDING * 2
                    }px`;
                    div.style.height = `${endTop + end.offsetHeight - top + FRAME_PADDING * 2}px`;
                    divContainer.prepend(div);
                    setTimeout(() => div.remove(), 2000);
                }
            },
            options: recordInfo,
            ...params,
        };
        // collapse to end
        const sanitize = this.dependencies.sanitize.sanitize;
        if (selection.isCollapsed) {
            this.dependencies.dialog.addDialog(QuestPromptDialog, { ...dialogParams });
        }
        if (this.services.ui.isSmall) {
            // TODO: Find a better way and avoid modifying range
            // HACK: In the case of opening through dropdown:
            // - when dropdown open, it keep the element focused before the open
            // - when opening the dialog through the dropdown, the dropdown closes
            // - upon close, the generic code of the dropdown sets focus on the kept element (in our case, the editable)
            // - we need to remove the range after the generic code of the dropdown is triggered so we hack it by removing the range in the next tick
            Promise.resolve().then(() => {
                // If the dialog is opened on a small screen, remove all selection
                // because the selection can be seen through the dialog on some devices.
                this.document.getSelection()?.removeAllRanges();
            });
        }
    }

}

MAIN_PLUGINS.push(QuestPlugin);