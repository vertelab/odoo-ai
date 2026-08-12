import { Component, useState, onWillStart } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";

export class OrgChartNode extends Component {
    static template = "ai_agent_core.OrgChartNode";
    static props = { node: Object, onOpen: Function };
}

export class OrgChartRenderer extends Component {
    static template = "ai_agent_core.OrgChartRenderer";
    static components = { OrgChartNode };
    static props = { archInfo: Object };

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.state = useState({ roots: [], loading: true, error: "" });
        onWillStart(async () => {
            try {
                const data = await this.orm.call(
                    "hr.department",
                    "org_chart_data",
                    []
                );
                this.state.roots = data.roots || [];
            } catch (err) {
                this.state.error = err.message || "Kunde inte ladda organisationen";
            } finally {
                this.state.loading = false;
            }
        });
    }

    openDepartment(node) {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: node.name,
            res_model: "hr.department",
            view_mode: "form",
            res_id: node.id,
        });
    }
}
