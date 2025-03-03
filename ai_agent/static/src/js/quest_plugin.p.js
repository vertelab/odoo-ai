import { _t } from "@web/core/l10n/translation";
import { Plugin } from "@html_editor/plugin";
import { closestElement } from "@html_editor/utils/dom_traversal";
import { QuestPromptDialog } from '@ai_agent/js/components/quest_prompt_dialog';
import { QuestSelectorDialog } from '@ai_agent/js/components/quest_selector_dialog';
import { withSequence } from "@html_editor/utils/resource";
import { MAIN_PLUGINS } from "@html_editor/plugin_sets";
import { rpc } from "@web/core/network/rpc";

export class QuestPlugin extends Plugin {
    static id = "quest";
    static dependencies = ["selection", "history", "dom", "sanitize", "dialog"];

    powerbox_items = [
        {
            title: _t("AI Quest"),
            description: _t("AI quest to perform operations"),
            categoryId: "ai_quest",
            commandId: "OpenQuest",
        }
    ];

    resources = {
        user_commands: [
            {
                id: "OpenQuest",
                title: _t("Select Quest"),
                description: _t("Pick a quest to perform operations"),
                icon: "fa-superpowers",
                run: this.openQuestSelector.bind(this),

            }
        ],
        powerbox_categories: withSequence(80, { id: "ai_quest", name: _t('AI Quest') }),
        powerbox_items: this.powerbox_items
    };

    async powerboxQuests() {
        const {resModel, resId } = this.config.getRecordInfo?.() || {}

        try {
            const powerbox_quests = await rpc('/web/dataset/call_kw', {
                model: 'ai.quest',
                method: 'search_read',
                args: [['|', ['model_id.model', '=', resModel], ['model_id', '=', false], ['init_type', '=', 'powerbox'], ['status', '=', 'active']]],
                kwargs: {
                    fields: ['id', 'name', 'sub_description']
                },
            }, { shadow: true });

            if (!Array.isArray(powerbox_quests)) {
                console.error('Unexpected response format:', powerbox_quests);
                return [];
            }

            return powerbox_quests;

        } catch (error) {
            console.error('Error fetching powerbox_items:', error);
            return [];
        }
    }

    async openQuestSelector() {
        const quests = await this.powerboxQuests();

        if (quests && quests.length == 0) {
            return alert("No Powerbox Quest Found");
        } else if (quests && quests.length == 1) {
            this.openChatDialog(quests[0])
        } else {
            this.openQuestSelectorDialog(quests)
        }
    }

    async openQuestSelectorDialog(quests) {
        const {resModel, resId } = this.config.getRecordInfo?.() || {}

        const selection = this.dependencies.selection.getEditableSelection();
        let restoreSelection = () => {
            this.dependencies.selection.setSelection(selection);
        };

        const dialogParams = {
            saveLink: (href) => {
                const templateBlock = renderToElement(
                    "ai_agent.QuestSelectorDialogBlueprint",
                    {
                        embeddedProps: JSON.stringify({ source: href }),
                    }
                );
                this.dependencies.dom.insert(templateBlock);
                this.dependencies.history.addStep();

                restoreSelection = () => {};
            },
            close: () => restoreSelection(),
            quests,
            pluginDependencies: this
        };
        this.services.dialog.add(QuestSelectorDialog, { ...dialogParams })
    }

    openChatDialog(quest, params = {}) {
        const {resModel, resId } = this.config.getRecordInfo?.() || {}

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
                    div.classList.add("o-quest-content");
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
            res_model: resModel,
            res_id: resId,
            quest,
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
