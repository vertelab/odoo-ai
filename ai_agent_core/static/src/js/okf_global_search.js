/** @odoo-module **/
/**
 * OKF Global Search — sökytan (dms-global-search D5, D6).
 *
 * VARFÖR: den enda användarvända sökingången i drift var en server action som
 * läste `env.context.get('search_query')` — en nyckel ingenting sätter — och
 * föll tillbaka på den bokstavliga strängen 'senaste', med `scope=None`.
 * `_okf_search` loggar det sistnämnda som "bred sökning" och returnerar
 * koncept över ALLA ägare.
 *
 * DEN HÄR KOMPONENTEN ÄR EN TUNN KLIENT. All ägarupplösning, dedup och
 * access-prövning sker i `ai.okf.concept.visible_to()` på servern. Klienten
 * får aldrig veta vilka scopes som finns — den frågar och får ett svar som
 * redan är filtrerat.
 *
 * SEMANTIK: `semantic=False` på tangentbordsvägen (D3). `_okf_search`
 * dokumenterar att ett embedding-anrop är 20 s × 3 försök — upp till 60 s för
 * en sökning när providern hänger. En sökruta som ibland tar en minut är sämre
 * än en som aldrig gör det. Den djupare sökningen är en EXPLICIT knapp, så
 * användaren väljer när hen betalar. Och ytan SÄGER att den är textsökning —
 * den låtsas inte om semantisk förståelse.
 */

import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { Component, useState, useRef, onWillStart } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";

const DEBOUNCE_MS = 250;

export class OkfGlobalSearch extends Component {
    static template = "ai_agent_core.OkfGlobalSearch";
    static props = {};

    setup() {
        super.setup();
        this.orm = useService("orm");
        this.action = useService("action");
        this.state = useState({
            query: "",
            groups: [],
            loading: false,
            semantic: false,
            searched: false,
            error: null,
        });
        this.input = useRef("input");
        this._timer = null;
        this._requestId = 0;
        onWillStart(() => {
            // Fokusera rutan när panelen öppnas — annars måste användaren
            // klicka två gånger för att börja skriva.
            requestAnimationFrame(() => {
                if (this.input.el) {
                    this.input.el.focus();
                }
            });
        });
    }

    // ── Sökning ──────────────────────────────────────────────────────

    onInput(ev) {
        this.state.query = ev.target.value;
        if (this._timer) {
            clearTimeout(this._timer);
        }
        if (!this.state.query.trim()) {
            this.state.groups = [];
            this.state.searched = false;
            this.state.error = null;
            return;
        }
        this._timer = setTimeout(() => this.search(), DEBOUNCE_MS);
    }

    onKeydown(ev) {
        if (ev.key === "Enter") {
            if (this._timer) {
                clearTimeout(this._timer);
            }
            this.search();
        } else if (ev.key === "Escape") {
            this.env.config?.onEscape?.();
        }
    }

    /**
     * Kör sökningen. `_requestId` skyddar mot att ett långsamt svar skriver
     * över ett snabbare: bara det senaste anropets svar får sätta state.
     */
    async search(semantic = null) {
        const query = this.state.query.trim();
        if (!query) {
            return;
        }
        if (semantic !== null) {
            this.state.semantic = semantic;
        }
        const requestId = ++this._requestId;
        this.state.loading = true;
        this.state.error = null;
        try {
            const groups = await this.orm.call(
                "ai.okf.concept",
                "visible_to_grouped",
                [query],
                {
                    limit: 30,
                    semantic: this.state.semantic,
                }
            );
            if (requestId !== this._requestId) {
                return;
            }
            this.state.groups = groups || [];
            this.state.searched = true;
        } catch (error) {
            if (requestId !== this._requestId) {
                return;
            }
            this.state.error =
                error?.data?.message || _t("The search could not be run.");
            this.state.groups = [];
            this.state.searched = true;
        } finally {
            if (requestId === this._requestId) {
                this.state.loading = false;
            }
        }
    }

    onSearchDeeper() {
        this.search(true);
    }

    onSearchTextOnly() {
        this.search(false);
    }

    // ── Resultat ─────────────────────────────────────────────────────

    get hasResults() {
        return this.state.groups.some((g) => g.concepts.length);
    }

    get totalCount() {
        return this.state.groups.reduce((n, g) => n + g.concepts.length, 0);
    }

    /**
     * Öppna källposten. Servern har redan prövat läsbarhet — `action` är
     * null när posten inte får läsas, och då visas ingen knapp.
     */
    async openConcept(concept) {
        if (!concept.action) {
            return;
        }
        await this.action.doAction(concept.action);
    }
}

registry.category("actions").add("dms_global_search", OkfGlobalSearch);
