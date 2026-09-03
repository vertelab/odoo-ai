/** @odoo-module **/
/**
 * html_editor-powerbox — "AI Medarbetare" i kategorin "AI-verktyg".
 *
 * Registrerar en html_editor-plugin som visar VARJE valbar powerbox-
 * medarbetare som eget kommando i kategorin "AI Tools" ("AI-verktyg"),
 * bredvid Odoos ChatGPT-kommando. Klickar man på en medarbetare körs
 * den direkt (utan mellansteget "välj medarbetare").
 *
 * Endast medarbetare med:
 *   - init_type powerbox + enabled
 *   - aktuell modell i model_ids (Target Models / powerbox_model_ids)
 * visas (strikt krav — inga "obegränsade"/alla-modeller eftersom lookup
 * med strikt filter returnerar bara modellmatchande).
 *
 * ResModel hämtas via getRecordInfo() på editor-config (HtmlField sätter
 * den), resultat infogas i markeringen via /ai/powerbox/run.
 */

import { _t } from "@web/core/l10n/translation";
import { Plugin } from "@html_editor/plugin";
import { patch } from "@web/core/utils/patch";
import { PowerboxPlugin } from "@html_editor/main/powerbox/powerbox_plugin";
import * as pluginSets from "@html_editor/plugin_sets";

/** Hämta powerbox-medarbetare (redan filtrerade på powerbox+modell i backend). */
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

/** Kör en powerbox-medarbetare med prompt; returnerar resultattexten. */
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

/** Hämta aktuell modell/res_id från editor-config. */
function getRecordContext(plugin) {
    let resModel = "", resId = null;
    if (typeof plugin.config?.getRecordInfo === "function") {
        const info = plugin.config.getRecordInfo();
        resModel = info?.resModel || "";
        resId = info?.resId ?? null;
    } else {
        const recordInfo = plugin.config?.recordInfo || {};
        resModel = recordInfo.res_model || recordInfo.resModel || "";
        resId = recordInfo.res_id || recordInfo.resId || null;
    }
    return { resModel, resId };
}

/** Infoga HTML vid markeringen. */
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

/** Visa prompt och kör en medarbetare. */
async function promptAndRun(plugin, quest, initialText) {
    const { resModel, resId } = getRecordContext(plugin);
    const prompt = window.prompt(
        _t(`Prompt för "${quest.name}":\n\nVad ska medarbetaren göra med texten?`),
        initialText || ""
    );
    if (prompt === null) return;
    try {
        const result = await runPowerbox(quest, prompt, resModel, resId);
        if (result) insertHtml(plugin, result);
        else window.alert(_t("Medarbetaren returnerade inget resultat."));
    } catch (e) {
        window.alert(_t("AI-fel: ") + (e.message || String(e)));
    }
}

export class AIPowerboxPlugin extends Plugin {
    static id = "ai_powerbox";
    static dependencies = ["selection", "dom", "history"];

    setup() {
        this._quests = [];
        // Pre-loada quests asynkront (cachade) — powerbox-commands läses
        // synkront (getAvailablePowerboxCommands kan inte vara async).
        this._loadQuests();
    }

    async _loadQuests() {
        try {
            const { resModel, resId } = getRecordContext(this);
            this._quests = await fetchPowerboxQuests(resModel, resId);
        } catch (e) {
            console.warn("powerbox preload failed:", e);
            this._quests = [];
        }
    }
}

// Hooka in i PowerboxPlugin: lägg till dynamiska per-medarbetare commands
// i "AI-verktyg"-kategorin. INTE async — search_powerbox_plugin anropar
// getAvailablePowerboxCommands() utan await (förväntar synkron array).
let aiPluginInstance = null;
patch(PowerboxPlugin.prototype, {
    getAvailablePowerboxCommands() {
        const baseCommands = this._super(...arguments);
        if (!aiPluginInstance || !aiPluginInstance._quests?.length) return baseCommands;

        // Lägg till ett kommando per medarbetare (i samma "ai"-kategori).
        const aiCommands = aiPluginInstance._quests.map((quest) => ({
            categoryId: "ai",
            title: quest.name,
            description: quest.sub_description || "",
            icon: "fa-superpowers",
            run: () => promptAndRun(aiPluginInstance, quest, ""),
        }));
        return [...baseCommands, ...aiCommands];
    },
});

// Registrera plugin i MAIN_PLUGINS så alla html_editor-instanser får den.
if (!pluginSets.MAIN_PLUGINS.find((p) => p.id === "ai_powerbox")) {
    pluginSets.MAIN_PLUGINS.push(AIPowerboxPlugin);
}
// Delad referens så patchen kan nå instansen.
pluginSets.AIPowerboxPlugin = AIPowerboxPlugin;
// i setup:n för patchen: hämta instans från plugin-sets (sätts via shared).
const originalSetup = AIPowerboxPlugin.prototype.setup;
AIPowerboxPlugin.prototype.setup = function (...args) {
    aiPluginInstance = this;
    if (originalSetup) originalSetup.apply(this, args);
};