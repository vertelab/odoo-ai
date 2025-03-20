import { threadActionsRegistry } from "@mail/core/common/thread_actions";
import { CallSettings } from "@mail/discuss/call/common/call_settings";

import { useComponent, useState } from "@odoo/owl";

import { _t } from "@web/core/l10n/translation";
import { useService } from "@web/core/utils/hooks";
import { rpc } from "@web/core/network/rpc";


const _get_discuss_channel_vals = async(channel_id) => {
    if (channel_id === 'undefined'){
        return
    }
    const channel_rec = await rpc('/web/dataset/call_kw', {
        model: 'discuss.channel',
        method: 'search_read',
        args: [[['id', '=', channel_id], ['ai_quest_id', '!=', false], ['ai_quest_id.ai_type', '=', 'fieldservice-order']]],
        kwargs: {
            limit: 1,
            fields: ['id', 'name', 'ai_quest_id']
        },
    }, { shadow: true });
    return channel_rec
}

const _get_related_field_service = async(ai_quest_id) => {
    const field_service = await rpc('/web/dataset/call_kw', {
        model: 'fieldservice.order',
        method: 'search_read',
        args: [[['ai_quest_id', '=', ai_quest_id], ['ai_quest_id.ai_type', '=', 'fieldservice-order']]],
        kwargs: {
            limit: 1,
            fields: ['id', 'name']
        },
    }, { shadow: true });
    return field_service
}


threadActionsRegistry
    .add("view_field_service", {
        async condition(component) {
            return (
                component.thread?.model === "discuss.channel" &&
                (!component.props.chatWindow || component.props.chatWindow.isOpen)
            );
        },
        icon: "fa fa-fw fa-eye",
        iconLarge: "fa fa-fw fa-lg fa-eye",
        name: _t("View Field Service"),
        async open(component) {
            if (component.thread?.model != "discuss.channel") {
                return alert("This is not a discuss channel")
            }

            const channel_rec = await _get_discuss_channel_vals(component.thread?.id)

            if (channel_rec && channel_rec.length == 0) {
                return alert("This channel is not connected to a Field Service")
            }

            const ai_quest_id = channel_rec[0].ai_quest_id[0]

            const field_service = await _get_related_field_service(ai_quest_id)

            if (field_service && field_service.length == 0) {
                return alert("Field Service does not exist. Contact Administrator for help.")
            }

            component.actionService.doAction({
                type: "ir.actions.act_window",
                res_id: field_service[0].id,
                res_model: "fieldservice.order",
                views: [[false, "form"]],
            });


        },
        sequence: 50,
        sequenceQuick: 50,
        setup() {
            const component = useComponent();
            component.rtc = useState(useService("discuss.rtc"));
        },
    })