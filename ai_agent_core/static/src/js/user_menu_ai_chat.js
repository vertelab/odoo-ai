/** @odoo-module **/
/**
 * AI Chat — user-menyn (längst upp till höger, bredvid Documentation/Support).
 *
 * Registrerar "AI Chat" i Odoo 18:s `user_menuitems`-registry så att alla
 * AI-användare (ai.group_user — implicit för alla via base.group_user) når
 * /ai/chat direkt från användarmenyn, utan att AI Orkestrering-menyn syns.
 */

import { registry } from "@web/core/registry";
import { browser } from "@web/core/browser/browser";
import { _t } from "@web/core/l10n/translation";

function aiChatItem() {
    const url = "/ai/chat";
    return {
        type: "item",
        id: "ai_chat",
        description: _t("AI Chat"),
        callback: () => {
            browser.location.href = url;
        },
        sequence: 15,
    };
}

registry.category("user_menuitems").add("ai_chat", aiChatItem);
