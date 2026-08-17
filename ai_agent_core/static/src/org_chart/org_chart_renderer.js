import { Component, useState, onWillStart } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";

const HEALTH_CLASS = {
    green: "text-bg-success",
    yellow: "text-bg-warning",
    red: "text-bg-danger",
};

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
                        flat.push({ ...n, depth });
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

    healthClass(health) {
        return HEALTH_CLASS[health] || "text-bg-secondary";
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

    async _openDepartmentMethod(node, method) {
        try {
            const action = await this.orm.call("hr.department", method, [[node.id]]);
            this.action.doAction(action);
        } catch (err) {
            console.warn(`org-chart: ${method} failed`, err);
        }
    }

    openTasks(node) {
        return this._openDepartmentMethod(node, "action_open_ai_tasks");
    }

    openGoals(node) {
        return this._openDepartmentMethod(node, "action_open_ai_goals");
    }
}
