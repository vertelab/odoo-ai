import { visitXML } from "@web/core/utils/xml";

export class OrgChartArchParser {
    parse(arch) {
        const archInfo = { fieldNames: [] };
        visitXML(arch, (node) => {
            if (node.tagName === "org_chart") {
                archInfo.title = node.getAttribute("string") || "Org Chart";
            }
        });
        return archInfo;
    }
}
