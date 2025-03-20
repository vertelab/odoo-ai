/** @odoo-module **/

import { ComponentWrapper } from 'web.OwlCompatibility';
import { qweb as QWeb, _t } from 'web.core';
import Wysiwyg from 'web_editor.wysiwyg';
import {descendants, preserveCursor} from "@web_editor/js/editor/odoo-editor/src/utils/utils";
import * as OdooEditorLib from "@web_editor/js/editor/odoo-editor/src/OdooEditor";
import { QuestPromptDialog } from '@ai_agent/js/components/quest_prompt_dialog';
import { QuestSelectorDialog } from '@ai_agent/js/components/quest_selector_dialog';
import { useService } from "@web/core/utils/hooks";
import { Component } from "@odoo/owl";
import { browser } from '@web/core/browser/browser';

const closestElement = OdooEditorLib.closestElement;
const OdooEditor = OdooEditorLib.OdooEditor;


Wysiwyg.include({

    _getPowerboxOptions: function () {
        const options = this._super();
        const {commands, categories} = options;

        commands.push({
            category: 'AI Tools',
            name: _t("AI Quest"),
            priority: 10,
            description: _t("Pick a quest to perform operations"),
            fontawesome: 'fa-superpowers',
            callback: async () => this.openQuestSelector(this)
        });

        return {...options, commands, categories};
    },

    powerboxQuests: async function() {
        const {res_model: resModel, res_id: resId } = this.options.recordInfo || {}

        try {
            const powerbox_quests = await this._rpc({
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
    },

    openQuestSelector: async function () {
        // Save the cursor position ONCE at the beginning of the flow
        this._savedRestore = preserveCursor(this.odooEditor.document);

        const quests = await this.powerboxQuests();

        if (quests && quests.length == 0) {
            return alert("No Powerbox Quest Found");
        } else if (quests && quests.length == 1) {
            this.openChatDialog(quests[0])
        } else {
            this.openQuestSelectorDialog(quests)
        }
    },

    openQuestSelectorDialog: async function(quests) {
        const {res_model: resModel, res_id: resId } = this.options.recordInfo || {}

        const dialogParams = {
            close: () => {
                // If dialog is closed without selection, restore cursor
                if (this._savedRestore) {
                    this._savedRestore();
                    this._savedRestore = null;
                }
            },
            quests,
            pluginDependencies: this
        };

        this.odooEditor.document.getSelection().collapseToEnd();
        Component.env.services.dialog.add(QuestSelectorDialog, { ...dialogParams });
    },

    openChatDialog: function(quest, params = {}) {
        // Use the saved restore if it exists, otherwise create a new one
        const restore = this._savedRestore || preserveCursor(this.odooEditor.document);
        // Clear the saved reference
        this._savedRestore = null;

        const {res_model: resModel, res_id: resId } = this.options.recordInfo || {}

        const dialogParams = {
            insert: content => {
                // First, check if the content needs markdown processing
                let processedContent = content;

                // If it doesn't already contain HTML formatting, convert markdown to HTML
                if (!content.includes('</p>') && !content.includes('</h') && !content.includes('</div>')) {
                    processedContent = this._markdownToHtml(content);
                }

                this.odooEditor.historyPauseSteps();

                // Create a temporary div to hold the HTML content
                const tempDiv = document.createElement('div');
                tempDiv.innerHTML = processedContent;

                // Insert the content as a document fragment instead of raw HTML
                const fragment = document.createDocumentFragment();
                while (tempDiv.firstChild) {
                    fragment.appendChild(tempDiv.firstChild);
                }

                // Use the execCommand to insert the fragment
                const insertedNodes = this.odooEditor.execCommand('insert', fragment);

                this.odooEditor.historyUnpauseSteps();
                this.odooEditor.historyStep();

                // Add a frame around the inserted content to highlight it for 2 seconds
                const start = insertedNodes?.length && closestElement(insertedNodes[0]);
                const end = insertedNodes?.length && closestElement(insertedNodes[insertedNodes.length - 1]);

                if (start && end) {
                    const divContainer = this.odooEditor.editable.parentElement;
                    let [parent, left, top] = [start.offsetParent, start.offsetLeft, start.offsetTop - start.scrollTop];
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
                    const div = document.createElement('div');
                    div.classList.add('o-quest-content');
                    const FRAME_PADDING = 3;
                    div.style.left = `${left - FRAME_PADDING}px`;
                    div.style.top = `${top - FRAME_PADDING}px`;
                    div.style.width = `${Math.max(start.offsetWidth, end.offsetWidth) + (FRAME_PADDING * 2)}px`;
                    div.style.height = `${endTop + end.offsetHeight - top + (FRAME_PADDING * 2)}px`;
                    divContainer.prepend(div);
                    setTimeout(() => div.remove(), 2000);
                }
            },
            res_model: resModel,
            res_id: resId,
            quest,
            ...params,
        };

        this.odooEditor.document.getSelection().collapseToEnd();
        Component.env.services.dialog.add(
            QuestPromptDialog,
            dialogParams,
            { onClose: restore },
        );
    },

    // Add the markdown to HTML conversion helper method
    _markdownToHtml: function(markdown) {
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
})