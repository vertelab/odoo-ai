/** @odoo-module **/

import { ComponentWrapper } from 'web.OwlCompatibility';
import { qweb as QWeb, _t } from 'web.core';
import Wysiwyg from 'web_editor.wysiwyg';
import {descendants, preserveCursor} from "@web_editor/js/editor/odoo-editor/src/utils/utils";
//import { ChatGPTPromptDialog } from '@ai_agent/js/wysiwyg/widgets/chatgpt_prompt_dialog';
import { QuestPromptDialog } from './widgets/quest_prompt_dialog';
import { useService } from "@web/core/utils/hooks";
import { Component } from "@odoo/owl";


Wysiwyg.include({

    openQuestDialog: function () {
        const restore = preserveCursor(this.odooEditor.document);

        const params = {
            insert: content => {
                this.odooEditor.historyPauseSteps();
                const insertedNodes = this.odooEditor.execCommand('insert', content);
                this.odooEditor.historyUnpauseSteps();
                this.notification.add(_t('Your content was successfully generated.'), {
                    title: _t('Content generated'),
                    type: 'success',
                });
                this.odooEditor.historyStep();
                // Add a frame around the inserted content to highlight it for 2
                // seconds.
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
                    div.classList.add('o-chatgpt-content');
                    const FRAME_PADDING = 3;
                    div.style.left = `${left - FRAME_PADDING}px`;
                    div.style.top = `${top - FRAME_PADDING}px`;
                    div.style.width = `${Math.max(start.offsetWidth, end.offsetWidth) + (FRAME_PADDING * 2)}px`;
                    div.style.height = `${endTop + end.offsetHeight - top + (FRAME_PADDING * 2)}px`;
                    divContainer.prepend(div);
                    setTimeout(() => div.remove(), 2000);
                }
            },
        };

        Component.env.services.dialog.add(
            QuestPromptDialog,
            params,
            { onClose: restore },
        );
    },

    _getPowerboxOptions: function () {
        const options = this._super();
        const {commands, categories} = options;
        console.log("_getPowerboxOptions", this)
        console.log("_getPowerboxOptions", this.options.recordInfo)
        console.log("fieldId", this.options.fieldId)
        commands.push({
            category: 'AI Tools',
            name: _t('Quest'),
            priority: 10,
            description: _t('Generate or transform content with AI Quest.'),
            fontawesome: 'fa-robot',
            callback: async () => this.openQuestDialog()
        });

        return {...options, commands, categories};
    },
})

