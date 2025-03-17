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
                // First, check if the content is already HTML
                let htmlContent = content;

                // If it's not HTML (doesn't have HTML tags), convert it to HTML
                if (!content.includes('</p>') && !content.includes('</h') && !content.includes('</div>')) {
                    // Process markdown-style content
                    htmlContent = this._markdownToHtml(content);
                }

                // Create a temporary div to hold the HTML content
                const tempDiv = document.createElement('div');
                tempDiv.innerHTML = htmlContent;

                // Insert each child node individually
                const insertedNodes = [];
                tempDiv.childNodes.forEach(node => {
                    // Need to use insert for each node
                    const nodes = this.dependencies.dom.insert(node.cloneNode(true));
                    if (nodes && nodes.length) {
                        insertedNodes.push(...nodes);
                    }
                });

                this.dependencies.history.addStep();

                // Add a frame around the inserted content to highlight it for 2 seconds.
                const start = insertedNodes?.length && closestElement(insertedNodes[0]);
                const end = insertedNodes?.length &&
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
            Promise.resolve().then(() => {
                this.document.getSelection()?.removeAllRanges();
            });
        }
    }

    // Add this helper method to convert markdown to HTML
    _markdownToHtml(markdown) {
        if (!markdown) return '';

        // Simple markdown conversion for common elements
        let html = markdown
            // Headers (must come before bold/italic)
            .replace(/^### (.*$)/gim, '<h3>$1</h3>')
            .replace(/^## (.*$)/gim, '<h2>$1</h2>')
            .replace(/^# (.*$)/gim, '<h1>$1</h1>')

            // Bold and italic
            .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
            .replace(/\*([^*]+)\*/g, '<em>$1</em>')

            // Code blocks
            .replace(/```([\s\S]*?)```/g, function(match, code) {
                return '<pre><code>' + code.trim().replace(/</g, '&lt;').replace(/>/g, '&gt;') + '</code></pre>';
            })

            // Inline code
            .replace(/`([^`]+)`/g, function(match, code) {
                return '<code>' + code.replace(/</g, '&lt;').replace(/>/g, '&gt;') + '</code>';
            });

        // Handle lists (bullet points)
        const bulletListPattern = /^(\s*)\* (.*?)$/gm;
        const bulletMatches = [...html.matchAll(bulletListPattern)];

        if (bulletMatches.length > 0) {
            // Collect all bullet points with their indentation levels
            const bulletItems = bulletMatches.map(match => ({
                indent: match[1].length,
                content: match[2],
                original: match[0]
            }));

            // Replace bullet points with HTML
            for (const item of bulletItems) {
                html = html.replace(item.original, `<li>${item.content}</li>`);
            }

            // Wrap in UL tags
            html = html.replace(/(<li>.*?<\/li>)+/gs, '<ul>$&</ul>');
        }

        // Handle numbered lists
        const numberedListPattern = /^(\s*)\d+\. (.*?)$/gm;
        const numberedMatches = [...html.matchAll(numberedListPattern)];

        if (numberedMatches.length > 0) {
            // Collect all numbered items with their indentation levels
            const numberedItems = numberedMatches.map(match => ({
                indent: match[1].length,
                content: match[2],
                original: match[0]
            }));

            // Replace numbered items with HTML
            for (const item of numberedItems) {
                html = html.replace(item.original, `<li>${item.content}</li>`);
            }

            // Wrap in OL tags
            html = html.replace(/(<li>.*?<\/li>)+/gs, '<ol>$&</ol>');
        }

        // Convert paragraphs (lines with content not already in HTML tags)
        // Split by double newlines to find paragraphs
        const paragraphs = html.split(/\n\s*\n/);
        html = paragraphs.map(p => {
            const trimmedP = p.trim();
            if (trimmedP && !trimmedP.startsWith('<')) {
                // Replace newlines with <br> tags within paragraphs
                return `<p>${trimmedP.replace(/\n/g, '<br>')}</p>`;
            }
            return trimmedP;
        }).join('\n');

        return html;
    }

}

MAIN_PLUGINS.push(QuestPlugin);
