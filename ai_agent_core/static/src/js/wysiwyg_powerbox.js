/** @odoo-module **/
/**
 * Web_editor-kommandopalett (Ctrl+K) — "AI-verktyg" → "AI Medarbetare".
 *
 * Registrerar ett kommando i web_editor:s _getPowerboxOptions (kategorin
 * "AI Tools" = "AI-verktyg") så att en användare i valfri text-editor kan
 * välja en powerbox-medarbetare (ai.coworker med init_type powerbox) och
 * låta den bearbeta markeringen / ett prompt.
 *
 * Hämtar tillgängliga coworkers via /ai/powerbox/lookup?model=<resModel>,
 * visar en dialog, kör /ai/powerbox/run och infogar resultatet.
 *
 * Ersätter den gamla (avinstallerade) ai_agent-modulens wysiwyg.js.
 */

import { patch } from "@web/core/utils/patch";
import { _t } from "@web/core/l10n/translation";
import { Wysiwyg } from "@web_editor/js/wysiwyg/wysiwyg";

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

/** Kör en powerbox-coworker med prompt och returnerar resultattexten. */
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

/** Infoga HTML vid markeringen i editorn. */
function insertAtCursor(wysiwyg, html) {
    const doc = wysiwyg.odooEditor?.document;
    const sel = doc?.getSelection?.();
    if (sel && sel.rangeCount > 0 && doc) {
        const node = doc.createElement("div");
        node.innerHTML = html;
        sel.getRangeAt(0).insertNode(node);
    }
}

/** Visa valdialog (enkelt prompt-flöde) för att välja medarbetare + prompt. */
async function selectAndRunQuest(wysiwyg) {
    const { res_model: resModel, res_id: resId } = wysiwyg.options?.recordInfo || {};
    const quests = await fetchPowerboxQuests(resModel, resId);
    if (!quests.length) {
        window.alert(_t("Inga AI-medarbetare (powerbox) tillgängliga för denna modell."));
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
        ""
    );
    if (prompt === null) return;

    try {
        const result = await runPowerbox(quest, prompt, resModel, resId);
        if (result) insertAtCursor(wysiwyg, result);
        else window.alert(_t("Medarbetaren returnerade inget resultat."));
    } catch (e) {
        window.alert(_t("AI-fel: ") + (e.message || String(e)));
    }
}

// Patch: lägg till kommandot i web_editor-powerboxen (Ctrl+K / '/' i editor).
patch(Wysiwyg.prototype, {
    _getPowerboxOptions() {
        const options = this._super(...arguments);
        const commands = options?.commands || [];
        commands.push({
            category: _t("AI Tools"),
            name: _t("AI Medarbetare"),
            description: _t("Välj en AI-medarbetare (powerbox) för att bearbeta text"),
            fontawesome: "fa-superpowers",
            priority: 11,
            callback: () => selectAndRunQuest(this),
        });
        return options;
    },
});