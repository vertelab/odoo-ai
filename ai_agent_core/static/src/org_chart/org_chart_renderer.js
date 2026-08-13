import { Component, useState, onWillStart } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";

export class OrgChartRenderer extends Component {
    static template = "ai_agent_core.OrgChartRenderer";
    static props = { archInfo: Object };

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.state = useState({ flat: [], loading: true, error: "" });
        onWillStart(async () => {
            try {
                const data = await this.orm.call(
                    "hr.department",
                    "org_chart_data",
                    []
                );
                const flat = [];
                const walk = (nodes, depth) => {
                    for (const n of nodes) {
                        flat.push({ id: n.id, name: n.name, ai_staff: n.ai_staff, goal_count: n.goal_count, depth });
                        if (n.child_ids && n.child_ids.length) {
                            walk(n.child_ids, depth + 1);
                        }
                    }
                };
                walk(data.roots || [], 0);
                this.state.flat = flat;
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
