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

/** Infoga HTML vid markeringen (utan att byta ut hela innehållet). */
function insertHtml(plugin, html) {
    const dom = plugin.dependencies.dom;
    const history = plugin.dependencies.history;
    if (history && typeof history.addStep === "function") history.addStep();
    try {
        dom.insert(html);
    } finally {
        if (history && typeof history.addStep === "function") history.addStep();
    }
}

/** Ersätt hela fältets innehåll med resultatet. */
function replaceAll(plugin, html) {
    const history = plugin.dependencies.history;
    const dom = plugin.dependencies.dom;
    const selection = plugin.dependencies.selection;
    if (history && typeof history.addStep === "function") history.addStep();
    try {
        // Välj allt i editable och infoga resultatet (dom.insert ersätter selektionen).
        selection.setSelection({ editable: plugin.editable, start: 0, end: plugin.editable.innerHTML.length });
        selection.setCollapsed();
        plugin.editable.innerHTML = "";
        const sel = selection.getEditableSelection();
        // Enklare: sätt innerHTML direkt (hela fältet)
        plugin.editable.innerHTML = html;
    } finally {
        if (history && typeof history.addStep === "function") history.addStep();
    }
}

/** Visa en overlay med förhandsvisning + knappar Ersätt / Lägg till / Avbryt. */
function showPreviewOverlay(plugin, result, originalHtml) {
    return new Promise((resolve) => {
        const overlay = document.createElement("div");
        overlay.style.cssText =
            "position:fixed;top:0;left:0;right:0;bottom:0;z-index:10000;background:rgba(0,0,0,.45);display:flex;align-items:center;justify-content:center;";
        const box = document.createElement("div");
        box.style.cssText =
            "background:#fff;border-radius:8px;max-width:720px;width:92%;max-height:85vh;display:flex;flex-direction:column;box-shadow:0 10px 40px rgba(0,0,0,.3);";
        const header = document.createElement("div");
        header.style.cssText = "padding:14px 18px;font-weight:600;border-bottom:1px solid #dee2e6;display:flex;justify-content:space-between;align-items:center;";
        header.textContent = _t("AI-förslag");
        const headerNote = document.createElement("small");
        headerNote.style.cssText = "color:#6c757d;";
        headerNote.textContent = _t("Granska förslaget innan du infogar");
        header.appendChild(headerNote);
        const body = document.createElement("div");
        body.style.cssText = "padding:16px 18px;overflow:auto;flex:1;border-bottom:1px solid #dee2e6;";
        body.innerHTML = result; // rendered — tillåter formatering
        const footer = document.createElement("div");
        footer.style.cssText = "padding:12px 18px;display:flex;gap:8px;justify-content:flex-end;";
        const btnReplace = document.createElement("button");
        btnReplace.textContent = _t("Ersätt");
        btnReplace.className = "btn btn-primary";
        const btnInsert = document.createElement("button");
        btnInsert.textContent = _t("Lägg till");
        btnInsert.className = "btn btn-secondary";
        const btnCancel = document.createElement("button");
        btnCancel.textContent = _t("Avbryt");
        btnCancel.className = "btn btn-outline-secondary";
        footer.append(btnReplace, btnInsert, btnCancel);

        const cleanup = () => overlay.remove();
        btnReplace.onclick = () => { cleanup(); resolve("replace"); };
        btnInsert.onclick = () => { cleanup(); resolve("insert"); };
        btnCancel.onclick = () => { cleanup(); resolve("cancel"); };
        overlay.onclick = (e) => { if (e.target === overlay) { cleanup(); resolve("cancel"); } };

        box.append(header, body, footer);
        overlay.appendChild(box);
        document.body.appendChild(overlay);
    });
}

/** Visa prompt och kör en medarbetare — med overlay (granska + Ersätt/Lägg till/Avbryt). */
async function promptAndRun(plugin, quest, initialText) {
    const { resModel, resId } = getRecordContext(plugin);
    const prompt = window.prompt(
        _t(`Prompt för "${quest.name}":\n\nVad ska medarbetaren göra med texten?`),
        initialText || ""
    );
    if (prompt === null) return;
    try {
        const result = await runPowerbox(quest, prompt, resModel, resId);
        if (!result) {
            window.alert(_t("Medarbetaren returnerade inget resultat."));
            return;
        }
        const originalHtml = plugin.editable?.innerHTML || "";
        const action = await showPreviewOverlay(plugin, result, originalHtml);
        if (action === "replace") {
            replaceAll(plugin, result);
        } else if (action === "insert") {
            insertHtml(plugin, result);
        }
        // "cancel" → gör inget
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
// OBS: getAvailablePowerboxCommands är en SHARED-metod (bindas via
// shared-mekanismen) → patch-`_super` injiceras inte — spara original-
// metoden explicit.
const _origGetAvailablePowerboxCommands =
    PowerboxPlugin.prototype.getAvailablePowerboxCommands;

let aiPluginInstance = null;
patch(PowerboxPlugin.prototype, {
    getAvailablePowerboxCommands() {
        const baseCommands = _origGetAvailablePowerboxCommands.call(this);
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