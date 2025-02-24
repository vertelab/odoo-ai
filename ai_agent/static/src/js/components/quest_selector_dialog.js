import { Dialog } from "@web/core/dialog/dialog";
import { Component, useState, markup, onWillDestroy, status, useExternalListener } from "@odoo/owl";
import { useAutofocus } from "@web/core/utils/hooks";
import { QuestPromptDialog } from '@ai_agent/js/components/quest_prompt_dialog';
import { closestElement } from "@html_editor/utils/dom_traversal";


export class QuestSelectorDialog extends Component {
    static template = "ai_agent.QuestSelectorDialog";
    static props = {
        close: Function,
        saveLink: Function,
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