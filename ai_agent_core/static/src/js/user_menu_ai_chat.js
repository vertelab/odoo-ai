/** @odoo-module **/
/**
 * AI Chat — user-menyn (längst upp till höger, bredvid Documentation/Support).
 *
 * Registrerar "AI Chat" + PWA-installationsposter i Odoo 18:s
 * `user_menuitems`-registry så att alla AI-användare (ai.group_user —
 * implicit för alla via base.group_user) når /ai/chat direkt från
 * användarmenyn, utan att AI Orkestrering-menyn syns.
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

function installAiChatItem() {
    return {
        type: "item",
        id: "install_ai_chat",
        description: _t("Installera AI Chat"),
        callback: () => {
            window.open("/ai/install?app=ai", "_blank");
        },
        sequence: 16,
    };
}

function installAppsItem() {
    return {
        type: "item",
        id: "install_apps",
        description: _t("Installera appar"),
        callback: () => {
            window.open("/ai/install", "_blank");
        },
        sequence: 17,
    };
}

registry.category("user_menuitems")
    .add("ai_chat", aiChatItem)
    .add("install_ai_chat", installAiChatItem)
    .add("install_apps", installAppsItem);
