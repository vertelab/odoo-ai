/** @odoo-module **/
import { Component, useState, onWillStart, useRef } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { registry } from "@web/core/registry";
import { standardActionServiceProps } from "@web/webclient/actions/action_service";
import { FolderTree } from "../components/folder_tree/folder_tree.esm.js";
import { MailList } from "../components/mail_list/mail_list.esm.js";
import { MailDetail } from "../components/mail_detail/mail_detail.esm.js";
import { Composer } from "../components/composer/composer.esm.js";

class MailImap extends Component {
    static template = "user_mail_imap.MailImap";
    static components = { FolderTree, MailList, MailDetail, Composer };
    static props = { ...standardActionServiceProps };

    setup() {
        this.store = useService("user_mail_imap.store");
        this.state = useState(this.store.getState());
        this.store.subscribe((s) => Object.assign(this.state, s));
        this.showComposer = useState({ visible: false, type: 'new', message: null });
        this.passwordInput = useRef("passwordInput");
        this.mailListContainer = useRef("mailListContainer");
        this._resizeActive = false;

        onWillStart(async () => {
            await this.store.checkPassword();
            if (this.state.hasPassword) {
                await this.store.loadFolders();
                await this.store.loadMessages('INBOX');
            }
        });
    }

    openPasswordPrompt() {
        this.state.showPasswordPrompt = true;
    }

    onPasswordKeydown(ev) {
        if (ev.key === 'Enter') {
            this.onSetPassword(ev.target.value);
        }
    }

    onPasswordButtonClick() {
        this.onSetPassword(this.passwordInput.el.value);
    }

    async onSetPassword(password) {
        await this.store.setPassword(password);
        this.state.showPasswordPrompt = false;
        await this.store.loadFolders();
        await this.store.loadMessages('INBOX');
    }

    async onFolderClick(folder) {
        this.state.currentFolder = folder;
        this.state.selectedMessage = null;
        await this.store.loadMessages(folder, 0);
    }

    async onMessageClick(uid) {
        await this.store.loadMail(this.state.currentFolder, uid);
    }

    openComposer(type, msg) {
        this.showComposer.visible = true;
        this.showComposer.type = type;
        this.showComposer.message = msg;
    }

    closeComposer() {
        this.showComposer.visible = false;
    }

    async onSendMail(data) {
        await this.store.sendMail(data.to, data.subject, data.body, data.cc);
        this.closeComposer();
    }

    loadMore() {
        if (!this.state.loading) {
            this.store.loadMessages(this.state.currentFolder, this.state.offset);
        }
    }

    async onSync() {
        await this.store.sync();
        await this.store.loadFolders(true);
        await this.store.loadMessages(this.state.currentFolder, 0);
    }

    startResize(ev) {
        ev.preventDefault();
        const container = this.mailListContainer.el;
        const startX = ev.clientX;
        const startWidth = container.offsetWidth;

        const onMove = (e) => {
            const newWidth = Math.max(200, Math.min(800, startWidth + (e.clientX - startX)));
            container.style.width = newWidth + 'px';
        };
        const onUp = () => {
            document.removeEventListener('mousemove', onMove);
            document.removeEventListener('mouseup', onUp);
            document.body.style.cursor = '';
            document.body.style.userSelect = '';
        };
        document.addEventListener('mousemove', onMove);
        document.addEventListener('mouseup', onUp);
        document.body.style.cursor = 'col-resize';
        document.body.style.userSelect = 'none';
    }
}

registry.category("actions").add("user_mail_imap.dashboard", MailImap);
