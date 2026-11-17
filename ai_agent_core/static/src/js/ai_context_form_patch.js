/** @odoo-module **/
/**
 * AI Context — FormController-patch.
 *
 * Ported from ai_agent_context/static/src/js/form_controller_patch.js
 * (2026-11-16).
 *
 * Systray-knappen vet inte själv vad användaren tittar på. Den frågar
 * FormController via bus: "vilken model och vilka ids har du?". Den här
 * patchen svarar.
 *
 * Använder manuell originalSetup i stället för @web/core/utils/patch:
 * patch() misslyckas binda this._super för ärvd setup() i OWL 2
 * (TypeError: this._super is not a function).
 */

import { FormController } from "@web/views/form/form_controller";
import { useEffect } from "@odoo/owl";

const originalSetup = FormController.prototype.setup;

if (originalSetup) {
    FormController.prototype.setup = function () {
        originalSetup.call(this);
        useEffect(
            () => {
                const onRequest = () => {
                    this.env.bus.trigger(
                        "AI_CONTEXT.SEND_MODEL_DETAILS", this.model
                    );
                };
                this.env.bus.addEventListener(
                    "AI_CONTEXT.REQUEST_MODEL_DETAILS", onRequest
                );
                return () =>
                    this.env.bus.removeEventListener(
                        "AI_CONTEXT.REQUEST_MODEL_DETAILS", onRequest
                    );
            },
            () => []
        );
    };
}
