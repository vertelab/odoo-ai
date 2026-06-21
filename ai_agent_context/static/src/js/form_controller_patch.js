/** @odoo-module */

import { patch } from "@web/core/utils/patch";
import { FormController } from "@web/views/form/form_controller";
import { useEffect } from "@odoo/owl";

/**
 * Patch FormController to respond to AI Quest context requests.
 *
 * When the Quest systray button is clicked, it triggers a bus event
 * requesting model details. This patch listens for that event and
 * responds with the current form's model object.
 *
 * Ported from Odoo Enterprise ai/static/src/web/form_controller_patch.js
 */
patch(FormController.prototype, {
    setup() {
        super.setup();
        useEffect(
            () => {
                const onDataRequest = (event) => {
                    // Send the current model to anyone requesting it
                    this.env.bus.trigger(
                        "AI_QUEST.SEND_MODEL_DETAILS",
                        this.model
                    );
                };
                this.env.bus.addEventListener(
                    "AI_QUEST.REQUEST_MODEL_DETAILS",
                    onDataRequest
                );
                return () =>
                    this.env.bus.removeEventListener(
                        "AI_QUEST.REQUEST_MODEL_DETAILS",
                        onDataRequest
                    );
            },
            () => []
        );
    },
});
