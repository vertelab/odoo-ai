/** @odoo-module **/
import { Component } from "@odoo/owl";

export class MailList extends Component {
    static template = "user_mail_imap.MailList";
    static props = {
        messages: { type: Array, optional: true },
        total: Number,
        loading: Boolean,
        selectedMessage: { type: Object, optional: true },
        currentFolder: String,
        onMessageClick: Function,
        loadMore: Function,
        openComposer: Function,
    };
}
