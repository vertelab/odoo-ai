import { registry } from "@web/core/registry";
import { OrgChartArchParser } from "./org_chart_arch_parser";
import { OrgChartController } from "./org_chart_controller";
import { OrgChartRenderer } from "./org_chart_renderer";

export const orgChartView = {
    type: "org_chart",
    Controller: OrgChartController,
    Renderer: OrgChartRenderer,
    ArchParser: OrgChartArchParser,

    props: (genericProps, view) => {
        const { arch } = genericProps;
        const archInfo = new view.ArchParser().parse(arch);
        return {
            ...genericProps,
            Renderer: view.Renderer,
            archInfo,
        };
    },
};

registry.category("views").add("org_chart", orgChartView);
