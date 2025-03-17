/** @odoo-module **/

import { Dialog } from "@web/core/dialog/dialog";
import { Component, useState, markup, onWillDestroy, status } from "@odoo/owl";
import { QuestPromptDialog } from '@ai_agent/js/components/quest_prompt_dialog';

export class QuestSelectorDialog extends Component {
    static template = "ai_agent.QuestSelectorDialog";
    static props = {
        close: Function,
        quests: false,
        pluginDependencies: false
    };
    static components = { Dialog };

    setup() {
        super.setup();
    }

    onQuestSelect(quest) {
        const { pluginDependencies } = this.props
        this.props.close()
        pluginDependencies.openChatDialog(quest)
    }
}