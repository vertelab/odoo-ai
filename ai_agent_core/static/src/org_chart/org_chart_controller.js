import { standardViewProps } from "@web/views/standard_view_props";
import { Layout } from "@web/search/layout";
import { Component } from "@odoo/owl";

export class OrgChartController extends Component {
    static template = "ai_agent_core.OrgChartView";
    static components = { Layout };
    static props = {
        ...standardViewProps,
        Renderer: Function,
        archInfo: Object,
    };

    get rendererProps() {
        return { archInfo: this.props.archInfo };
    }
}
