# -*- coding: utf-8 -*-
"""Registrera org_chart som egen vytyp (den installerade Odoo-web saknar
hierarchy-vytypen, så AI Orkestrering får en egen org-chart istället)."""

from odoo import fields, models


class IrUiView(models.Model):
    _inherit = 'ir.ui.view'

    type = fields.Selection(selection_add=[('org_chart', 'Org Chart')])

    def _postprocess_tag_org_chart(self, node, name_manager, node_info):
        pass

    def _get_view_info(self):
        return {'org_chart': {'icon': 'fa fa-sitemap'}} | super()._get_view_info()
