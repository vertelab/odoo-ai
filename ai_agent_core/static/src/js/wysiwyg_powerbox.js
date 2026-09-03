/** @odoo-module **/
/**
 * html_editor-powerbox — "AI Medarbetare" i kategorin "AI-verktyg".
 *
 * Registrerar en html_editor-plugin som lägger till ett powerbox-kommando
 * "AI Medarbetare" i samma "AI Tools"-kategori ("AI-verktyg") som Odoos
 * ChatGPT-kommando. Det gör att man i valfri HtmlField/description-editor
 * (t.ex. ai.coworker Description) kan välja en powerbox-medarbetare
 * (ai.coworker med init_type powerbox) och låta den bearbeta texten.
 *
 * Hämtar medarbetare via /ai/powerbox/lookup och kör /ai/powerbox/run.
 */

import { _t } from "@web/core/l10n/translation";
import { Plugin } from "@html_editor/plugin";
import * as pluginSets from "@html_editor/plugin_sets";

/** Hämta powerbox-coworkers för aktuell modell/record. */
async function fetchPowerboxQuests(resModel, resId) {
    const url = `/ai/powerbox/lookup?model=${encodeURIComponent(resModel || "")}` +
        (resId ? `&res_id=${encodeURIComponent(resId)}` : "");
    try {
        const resp = await fetch(url, { signal: AbortSignal.timeout(15000) });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const data = await resp.json();
        return data.quests || [];
    } catch (e) {
        console.warn("powerbox lookup failed:", e);
        return [];
    }
}

/** Kör en powerbox-coworker med prompt; returnerar resultattexten. */
async function runPowerbox(quest, prompt, resModel, resId) {
    const resp = await fetch("/ai/powerbox/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
            quest_id: quest.id,
            text: prompt,
            model: resModel,
            res_id: resId,
        }),
        signal: AbortSignal.timeout(120000),
    });
    const data = await resp.json();
    if (data.error) throw new Error(data.error);
    return data.result || "";
}

/** Infoga HTML vid markeringen (via pluginens selection/history). */
function insertHtml(plugin, html) {
    const selection = plugin.dependencies.selection.getEditableSelection();
    const dom = plugin.dependencies.dom;
    const history = plugin.dependencies.history;
    if (history) history.step();
    try {
        dom.insert(selection, html);
    } finally {
        if (history) history.step();
    }
}

export class AIPowerboxPlugin extends Plugin {
    static id = "ai_powerbox";
    static dependencies = ["selection", "dom", "history", "dialog"];

    resources = {
        user_commands: [
            {
                id: "openAIPowerbox",
                title: _t("AI Medarbetare"),
                description: _t("Välj en AI-medarbetare (powerbox) för att bearbeta text"),
                icon: "fa-superpowers",
                run: this.openPowerbox.bind(this),
            },
        ],
        // Delar kategorin "ai" (AI Tools / AI-verktyg) med ChatGPT-kommandot
        // — lägg inte till en ny kategori (undvik duplikat).
        powerbox_items: [
            {
                categoryId: "ai",
                commandId: "openAIPowerbox",
                keywords: [_t("AI"), _t("medarbetare"), _t("powerbox")],
            },
        ],
    };

    async openPowerbox() {
        const selection = this.dependencies.selection.getEditableSelection();
        // Aktuell modell/res_id hämtas via getRecordInfo (HtmlField ställer
        // in den i editor-config) — fallback till config-fält.
        let resModel = "", resId = null;
        if (typeof this.config?.getRecordInfo === "function") {
            const info = this.config.getRecordInfo();
            resModel = info?.resModel || "";
            resId = info?.resId ?? null;
        } else {
            const recordInfo = this.config?.recordInfo || {};
            resModel = recordInfo.res_model || recordInfo.resModel || "";
            resId = recordInfo.res_id || recordInfo.resId || null;
        }

        const quests = await fetchPowerboxQuests(resModel, resId);
        if (!quests.length) {
            this.dependencies.dialog.add?.({
                title: _t("AI Medarbetare"),
                body: _t("Inga AI-medarbetare (powerbox) tillgängliga för denna modell."),
            });
            return;
        }

        let quest = quests[0];
        if (quests.length > 1) {
            const lines = quests.map((q, i) => `${i + 1}. ${q.name}`).join("\n");
            const chosen = window.prompt(
                _t("AI Medarbetare — välj:\n\n") + lines + _t("\n\nNummer (1-" + quests.length + "):"),
                "1"
            );
            if (!chosen) return;
            const idx = parseInt(chosen, 10) - 1;
            if (isNaN(idx) || idx < 0 || idx >= quests.length) {
                window.alert(_t("Ogiltigt val."));
                return;
            }
            quest = quests[idx];
        }

        const prompt = window.prompt(
            _t(`Prompt för "${quest.name}":\n\nVad ska medarbetaren göra med texten?`),
            selection.textContent() || ""
        );
        if (prompt === null) return;

        try {
            const result = await runPowerbox(quest, prompt, resModel, resId);
            if (result) {
                insertHtml(this, result);
            } else {
                window.alert(_t("Medarbetaren returnerade inget resultat."));
            }
        } catch (e) {
            window.alert(_t("AI-fel: ") + (e.message || String(e)));
        }
    }
}

// Registrera plugin i MAIN_PLUGINS så alla html_editor-instanser får den.
if (!pluginSets.MAIN_PLUGINS.find((p) => p.id === "ai_powerbox")) {
    pluginSets.MAIN_PLUGINS.push(AIPowerboxPlugin);
}