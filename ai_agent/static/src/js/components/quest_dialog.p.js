/** @odoo-module **/

import { Component, useState, markup, onWillDestroy, status } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { Dialog } from "@web/core/dialog/dialog";
import { escape } from "@web/core/utils/strings";
import { _t } from "@web/core/l10n/translation";
import { browser } from '@web/core/browser/browser';
// #if VERSION >= '18.0'
import { rpc } from "@web/core/network/rpc";
// #endif

/**
 * General component for common logic between different dialogs.
 */
export class QuestDialog extends Component {
    static components = { Dialog };
    static props = {
        close: Function,
        insert: Function,
        res_model: false,
        res_id: false,
        quest: false
    };

    setup() {
        // #if VERSION <= '16.0'
        this.rpc = useService('rpc');
        // #endif
        this.state = useState({ selectedMessageId: null });
        onWillDestroy(() => this.pendingRpcPromise?.abort());
    }

    //--------------------------------------------------------------------------
    // Public
    //--------------------------------------------------------------------------

    selectMessage(ev) {
        this.state.selectedMessageId = +ev.currentTarget.getAttribute('data-message-id');
    }

    insertMessage(ev) {
        try {
            // Get the message ID from the clicked button
            const messageId = ev.currentTarget.getAttribute('data-message-id');

            // Find the message content
            const message = this.state.messages.find(msg => msg.id === Number(messageId));

            if (!message || !message.text) {
                console.error("Message not found or has no text content");
                return;
            }

            // Close the dialog
            this.props.close();

            // Pass the raw text directly to the insert function
            // This ensures the content isn't processed further
            this.props.insert(message.text);
        } catch (e) {
            console.error("Error inserting message:", e);
            this.props.close();
        }
    }

    formatContent(content) {
        return markup(content);
    }

    //--------------------------------------------------------------------------
    // Private
    //--------------------------------------------------------------------------

    _postprocessGeneratedContent(content) {
        // Don't process the content at all, just return it as is
        // This preserves all formatting including markdown
        const fragment = document.createDocumentFragment();
        const div = document.createElement('div');
        div.innerHTML = content;

        // Append all child nodes from the div to the fragment
        while (div.firstChild) {
            fragment.appendChild(div.firstChild);
        }
        return fragment;
    }

    _cancel() {
        this.props.close();
    }

    _confirm() {
        try {
            this.props.close();
            const text = this.state.messages.find(message => message.id === this.state.selectedMessageId)?.text;

            // Just pass the raw text without processing
            this.props.insert(text || '');
        } catch (e) {
            this.props.close();
            throw e;
        }
    }

    async _generate(prompt, callback) {
        const protectedCallback = (...args) => {
            if (status(this) !== 'destroyed') {
                delete this.pendingRpcPromise;
                return callback(...args);
            }
        }

        const { quest, res_model,res_id } = this.props
        // #if VERSION <= '16.0'
        this.pendingRpcPromise = this.rpc('/web/dataset/call_kw', {
        // #else
        this.pendingRpcPromise = rpc('/web/dataset/call_kw', {
        // #endif
            model: 'ai.quest',
            method: 'powerbox',
            args: [[quest, prompt, res_model, res_id]],
            kwargs: { quest, prompt, res_model, res_id },
        }, { shadow: true });

        return this.pendingRpcPromise
            .then(content => protectedCallback(content))
            .catch(error => protectedCallback(_t(error.data?.message || error.message), true));
    }
}
