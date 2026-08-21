/** @odoo-module **/
import { Component } from "@odoo/owl";

export class MailDetail extends Component {
    static template = "user_mail_imap.MailDetail";
    static props = {
        message: { type: Object, optional: true },
        loading: { type: Boolean, optional: true },
        openComposer: Function,
    };
}
