/** @odoo-module **/
/**
 * Powerbox — Slash command for AI quests in text fields.
 *
 * When the user types "/" in a text/html field, a dropdown appears
 * showing available powerbox quests for the current record's model.
 *
 * Select a quest → text is sent to the AI → result replaces the text.
 */

import { registry } from "@web/core/registry";
import { patch } from "@web/core/utils/patch";
import { TextField } from "@web/views/fields/text/text_field";
import { HtmlField } from "@web/views/fields/html/html_field";
import { useService } from "@web/core/utils/hooks";
import { Component, useState, onWillDestroy, xml } from "@odoo/owl";

// ---------------------------------------------------------------------------
// Powerbox Dropdown Component
// ---------------------------------------------------------------------------

class PowerboxDropdown extends Component {
    static template = xml`
        <div class="powerbox-dropdown o-dropdown-menu show"
             t-att-style="'position:fixed;left:' + props.position.left + 'px;top:' + props.position.top + 'px;z-index:1050;min-width:250px;'">
            <t t-foreach="props.quests" t-as="q" t-key="q.id">
                <a class="dropdown-item d-flex align-items-center gap-2 py-2"
                   t-att-class="{ 'active': state.selectedIndex === q_index }"
                   t-on-click="() => props.onSelect(q)">
                    <span class="fw-bold" t-out="q.name"/>
                    <small class="text-muted ms-2" t-out="q.sub_description"/>
                </a>
            </t>
            <t t-if="!props.quests.length">
                <span class="dropdown-item text-muted">No powerbox quests available</span>
            </t>
        </div>
    `;
    static props = {
        quests: { type: Array },
        onSelect: { type: Function },
        onClose: { type: Function },
        position: { type: Object },
    };

    setup() {
        this.state = useState({
            selectedIndex: 0,
        });
        // Close on Escape
        this._onKeyDown = (ev) => {
            if (ev.key === "Escape") {
                this.props.onClose();
            } else if (ev.key === "ArrowDown") {
                ev.preventDefault();
                this.state.selectedIndex = Math.min(
                    this.state.selectedIndex + 1,
                    this.props.quests.length - 1
                );
            } else if (ev.key === "ArrowUp") {
                ev.preventDefault();
                this.state.selectedIndex = Math.max(
                    this.state.selectedIndex - 1,
                    0
                );
            } else if (ev.key === "Enter") {
                ev.preventDefault();
                const quest = this.props.quests[this.state.selectedIndex];
                if (quest) {
                    this.props.onSelect(quest);
                }
            }
        };
        document.addEventListener("keydown", this._onKeyDown);
        onWillDestroy(() => {
            document.removeEventListener("keydown", this._onKeyDown);
        });
    }
}

// ---------------------------------------------------------------------------
// Templates (inline — avoids QWeb template loading issues)
// ---------------------------------------------------------------------------

// PowerboxDropdown.template = "ai_agent_core.PowerboxDropdown";  -- replaced by inline xml`

// ---------------------------------------------------------------------------
// Powerbox mixin — adds slash command to text/html fields
// ---------------------------------------------------------------------------

const powerboxMixin = {
    /**
     * Intercept "/" in text fields and show powerbox dropdown.
     */
    setup() {
        // Call original setup if exists
        if (this._super_setup) {
            this._super_setup();
        }

        this.orm = useService("orm");
        this._powerboxDropdown = null;
        this._powerboxActive = false;
        this._powerboxQuests = [];
        this._powerboxModel = "";
        this._powerboxResId = null;
        this._powerboxFieldValue = "";

        // Detect current model/record from context
        this._detectModel();
    },

    _detectModel() {
        // Get model and res_id from the current view context.
        // Odoo 18: record.resModel/resId är den pålitliga källan i
        // form-views; context.active_model kan vara tom i fältkomponenter.
        const record = this.props?.record || {};
        const context = this.props?.context || this.env?.searchModel?.context || {};
        this._powerboxModel =
            record.resModel ||
            context.active_model ||
            this.env?.searchModel?.resModel ||
            this.props?.model ||
            "";
        this._powerboxResId =
            record.resId ?? context.active_id ?? null;
    },

    /**
     * Called when a slash command dropdown should appear.
     */
    async _openPowerbox(fieldValue) {
        if (this._powerboxActive) return;
        this._powerboxActive = true;
        this._powerboxFieldValue = fieldValue || "";

        // Hämta färsk modell/res_id vid varje öppning — record kan vara sent känd.
        this._detectModel();

        try {
            // Fetch available powerbox quests for this model
            const result = await fetch(
                `/ai/powerbox/lookup?model=${encodeURIComponent(this._powerboxModel)}` +
                (this._powerboxResId ? `&res_id=${this._powerboxResId}` : "")
            );
            const data = await result.json();
            this._powerboxQuests = data.quests || [];

            if (this._powerboxQuests.length > 0) {
                // Get cursor position for dropdown placement
                const el = this._getActiveElement();
                const rect = el?.getBoundingClientRect?.() || { left: 100, top: 200 };

                // Show dropdown via custom event (handled by a global overlay)
                this.env.bus.trigger("powerbox:open", {
                    quests: this._powerboxQuests,
                    position: { left: rect.left, top: rect.bottom + 4 },
                    onSelect: this._onPowerboxSelect.bind(this),
                    onClose: this._closePowerbox.bind(this),
                });
            }
        } catch (e) {
            console.warn("Powerbox lookup failed:", e);
            this._powerboxActive = false;
        }
    },

    _getActiveElement() {
        return document.activeElement;
    },

    _closePowerbox() {
        this._powerboxActive = false;
        this._powerboxQuests = [];
        this.env.bus.trigger("powerbox:close");
    },

    /**
     * User selected a quest from the dropdown.
     */
    async _onPowerboxSelect(quest) {
        const text = this._powerboxFieldValue || "";
        const model = this._powerboxModel;
        const resId = this._powerboxResId;

        this._closePowerbox();

        if (!text.trim()) return;

        try {
            // Show loading indicator
            this._showLoading();

            const result = await fetch("/ai/powerbox/run", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    quest_id: quest.id,
                    text: text,
                    model: model,
                    res_id: resId,
                }),
            });
            const data = await result.json();

            this._hideLoading();

            if (data.result) {
                // Replace field content with AI result
                this._setFieldValue(data.result);
            } else if (data.error) {
                this._showNotification(data.error, "danger");
            }
        } catch (e) {
            this._hideLoading();
            this._showNotification("Powerbox error: " + e.message, "danger");
        }
    },

    _showLoading() {
        this.env.bus?.trigger?.("powerbox:loading", true);
    },

    _hideLoading() {
        this.env.bus?.trigger?.("powerbox:loading", false);
    },

    _setFieldValue(value) {
        // Subclasses should override or we use a generic approach
        if (this.props?.update) {
            this.props.update(value);
        } else if (this.props?.record?.update) {
            this.props.record.update({ [this.props.name]: value });
        }
    },

    _showNotification(message, type) {
        if (this.env?.services?.notification) {
            this.env.services.notification.add(message, { type });
        }
    },
};

// ---------------------------------------------------------------------------
// Patch TextField and HtmlField with powerbox support
// ---------------------------------------------------------------------------

function addPowerboxSupport(FieldClass) {
    const originalSetup = FieldClass.prototype.setup;
    FieldClass.prototype.setup = function () {
        this._super_setup = originalSetup?.bind?.(this);
        if (originalSetup) {
            originalSetup.call(this);
        }
        Object.assign(this, powerboxMixin);
        this.setup.call(this);
    };

    const originalMounted = FieldClass.prototype.mounted || (() => {});
    FieldClass.prototype.mounted = function () {
        originalMounted.call(this);
        this._detectModel();

        // Add keydown listener for "/"
        const handler = (ev) => {
            const el = ev.target;
            if (!el) return;

            const isTextArea = el.tagName === "TEXTAREA";
            const isContentEditable = el.getAttribute?.("contenteditable") === "true";
            if (!isTextArea && !isContentEditable) return;

            if (ev.key === "/" && !this._powerboxActive) {
                // Only trigger at start of line or with preceding space
                const cursorPos = el.selectionStart;
                const textBefore = (el.value || el.textContent || "").substring(0, cursorPos);
                const atStart = cursorPos === 0 || textBefore.endsWith("\n") || textBefore.endsWith(" ");

                if (atStart) {
                    ev.preventDefault();
                    const fullText = el.value || el.textContent || "";
                    const textAfterCursor = fullText.substring(cursorPos);
                    // Store text without the "/" for processing
                    this._openPowerbox(textAfterCursor);
                }
            }
        };

        // Find the actual input element in the DOM
        const el = this.__owl__?.hostElement || this.el;
        if (el) {
            const textarea = el.querySelector("textarea, [contenteditable='true']");
            if (textarea) {
                textarea.addEventListener("keydown", handler);
                this._powerboxHandler = { el: textarea, handler };
            }
        }
    };

    const originalWillUnmount = FieldClass.prototype.willUnmount || (() => {});
    FieldClass.prototype.willUnmount = function () {
        originalWillUnmount.call(this);
        if (this._powerboxHandler) {
            this._powerboxHandler.el.removeEventListener(
                "keydown",
                this._powerboxHandler.handler
            );
        }
        this._closePowerbox?.();
    };
}

addPowerboxSupport(TextField);
addPowerboxSupport(HtmlField);

// ---------------------------------------------------------------------------
// Global Powerbox Overlay (handles the dropdown rendering)
// ---------------------------------------------------------------------------

class PowerboxOverlay extends Component {
    static template = xml`<div/>`;
    static components = { PowerboxDropdown };
    static props = {};

    setup() {
        this.state = useState({
            visible: false,
            quests: [],
            position: { left: 0, top: 0 },
            onSelect: null,
            onClose: null,
            loading: false,
        });

        this.env.bus.addEventListener("powerbox:open", (ev) => {
            Object.assign(this.state, ev.detail || ev, { visible: true, loading: false });
            this.render();
        });

        this.env.bus.addEventListener("powerbox:close", () => {
            this.state.visible = false;
            this.render();
        });

        this.env.bus.addEventListener("powerbox:loading", (ev) => {
            this.state.loading = ev.detail !== false;
            this.render();
        });

        // Click outside to close
        this._onClickOutside = (ev) => {
            if (this.state.visible && !ev.target.closest(".powerbox-overlay")) {
                this.state.onClose?.();
                this.state.visible = false;
                this.render();
            }
        };
        document.addEventListener("click", this._onClickOutside);
        onWillDestroy(() => {
            document.removeEventListener("click", this._onClickOutside);
        });
    }
}

PowerboxOverlay.template = xml`
    <div class="powerbox-overlay" t-if="state.visible">
        <PowerboxDropdown
            quests="state.quests"
            position="state.position"
            onSelect="state.onSelect"
            onClose="state.onClose"/>
        <div t-if="state.loading" class="powerbox-loading" style="position:fixed;top:50%;left:50%;z-index:1060;">
            <span>AI processing...</span>
        </div>
    </div>
`;

// Remove old template assignments
// PowerboxOverlay.template = "ai_agent_core.PowerboxOverlay";  -- replaced by inline xml`

// ---------------------------------------------------------------------------
// Register as main component
// ---------------------------------------------------------------------------

registry.category("main_components").add("PowerboxOverlay", {
    Component: PowerboxOverlay,
    props: {},
});
