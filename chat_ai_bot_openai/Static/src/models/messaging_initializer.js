/** @odoo-module **/

import { registerPatch } from '@mail/model/model_core';
import { insert } from '@mail/model/model_field_command';

registerPatch({
    name: 'MessagingInitializer',
    recordMethods: {
        /**
         * @override
         */
        _initCommands() {
            this._super();
            this.messaging.update({
                commands: insert({
                    help: this.env._t("Ask something to the AI"),
                    methodName: 'execute_command_ai',
                    name: "ai",
                }),
            });
        },
    },
});
